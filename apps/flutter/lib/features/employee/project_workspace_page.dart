import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';
import 'package:prodavan/features/employee/project_settings_page.dart';
import 'package:prodavan/features/employee/tool_approve_page.dart';

/// Chat-first project workspace (L05/L09) — SSE streaming assistant deltas.
class ProjectWorkspacePage extends StatefulWidget {
  const ProjectWorkspacePage({
    super.key,
    required this.projectId,
    required this.projectName,
    required this.cabinetId,
  });

  final String projectId;
  final String projectName;
  final String cabinetId;

  @override
  State<ProjectWorkspacePage> createState() => _ProjectWorkspacePageState();
}

class _ChatLine {
  _ChatLine({
    required this.role,
    required this.text,
    this.streaming = false,
    this.approvalId,
    this.toolName,
    this.toolInput,
    this.attachmentRefs = const [],
  });

  final String role;
  final String text;
  final bool streaming;
  final String? approvalId;
  final String? toolName;
  final Map<String, dynamic>? toolInput;
  final List<String> attachmentRefs;
}

class _PendingAttachment {
  const _PendingAttachment({
    required this.id,
    required this.ref,
    required this.filename,
  });

  final String id;
  final String ref;
  final String filename;
}

class _ProjectWorkspacePageState extends State<ProjectWorkspacePage> {
  final _composer = TextEditingController();
  final _scroll = ScrollController();
  final _messages = <_ChatLine>[];
  final _pendingAttachments = <_PendingAttachment>[];
  final _inboxAttachments = <Map<String, dynamic>>[];
  String? _sessionId;
  late String _projectName;
  bool _loading = true;
  bool _sending = false;
  bool _uploadingAttachment = false;
  bool _cancelRequested = false;
  bool _companySuspended = false;
  bool _projectPaused = false;
  String? _error;
  ProjectChatStreamHandle? _activeStream;

  bool get _chatBlocked => _companySuspended || _projectPaused;

  @override
  void initState() {
    super.initState();
    _projectName = widget.projectName;
    workContext.enterProject(widget.projectId);
    _loadTranscript();
  }

  @override
  void dispose() {
    _activeStream?.abort();
    _composer.dispose();
    _scroll.dispose();
    super.dispose();
  }

  Future<void> _pickAttachment() async {
    if (_uploadingAttachment || _sending || _loading || _chatBlocked) return;
    final result = await FilePicker.platform.pickFiles(withData: true);
    if (result == null || result.files.isEmpty) return;
    final file = result.files.first;
    final bytes = file.bytes;
    if (bytes == null) {
      if (!mounted) return;
      setState(() => _error = 'Could not read file bytes');
      return;
    }
    final filename = file.name.trim().isEmpty ? 'attachment.bin' : file.name.trim();
    setState(() {
      _uploadingAttachment = true;
      _error = null;
    });
    try {
      final uploaded = await workContext.api.uploadProjectAttachment(
        projectId: widget.projectId,
        filename: filename,
        bytes: bytes,
      );
      if (!mounted) return;
      final ref = uploaded['storage_ref'] as String?;
      final id = uploaded['id'] as String?;
      if (ref == null || ref.isEmpty || id == null || id.isEmpty) {
        setState(() => _error = 'Upload missing id/storage_ref');
        return;
      }
      setState(() {
        _pendingAttachments.add(_PendingAttachment(id: id, ref: ref, filename: filename));
      });
      await _loadInbox();
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _uploadingAttachment = false);
    }
  }

  Future<void> _removeAttachment(_PendingAttachment item) async {
    setState(() => _pendingAttachments.remove(item));
    try {
      await workContext.api.deleteProjectAttachment(
        projectId: widget.projectId,
        attachmentId: item.id,
      );
      await _loadInbox();
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = e.toString());
    }
  }

  Future<void> _deleteInboxAttachment(Map<String, dynamic> item) async {
    final id = item['id'] as String?;
    if (id == null || id.isEmpty) return;
    setState(() {
      _inboxAttachments.removeWhere((a) => a['id'] == id);
      _pendingAttachments.removeWhere((a) => a.id == id);
    });
    try {
      await workContext.api.deleteProjectAttachment(
        projectId: widget.projectId,
        attachmentId: id,
      );
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = e.toString());
      await _loadInbox();
    }
  }

  Future<void> _loadInbox() async {
    try {
      final items = await workContext.api.listProjectAttachments(widget.projectId);
      if (!mounted) return;
      setState(() {
        _inboxAttachments
          ..clear()
          ..addAll(items);
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = e.toString());
    }
  }

  String _attachmentLabel(String ref) {
    final slash = ref.replaceAll('\\', '/').lastIndexOf('/');
    if (slash >= 0 && slash < ref.length - 1) {
      return ref.substring(slash + 1);
    }
    return ref.length > 24 ? '${ref.substring(0, 21)}…' : ref;
  }

  Future<void> _loadTranscript() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final project = await workContext.api.getProject(widget.projectId);
      final sub = project['company_subscription'] as Map<String, dynamic>? ?? const {};
      final result = await workContext.api.projectChatTranscript(projectId: widget.projectId);
      if (!mounted) return;
      final items = result['messages'];
      final lines = <_ChatLine>[];
      if (items is List) {
        for (final raw in items) {
          if (raw is! Map) continue;
          final role = raw['role'] as String? ?? 'assistant';
          final text = raw['text'] as String? ?? '';
          final refsRaw = raw['attachment_refs'];
          final refs = refsRaw is List
              ? refsRaw.map((e) => e.toString()).where((s) => s.isNotEmpty).toList()
              : const <String>[];
          if (text.isEmpty && refs.isEmpty) continue;
          final input = raw['input'];
          lines.add(
            _ChatLine(
              role: role,
              text: text.isEmpty && refs.isNotEmpty ? '(attachment)' : text,
              approvalId: raw['approval_id'] as String?,
              toolName: raw['tool_name'] as String?,
              toolInput: input is Map<String, dynamic> ? input : null,
              attachmentRefs: refs,
            ),
          );
        }
      }
      setState(() {
        _sessionId = result['session_id'] as String?;
        _companySuspended = sub['subscription_expired'] == true;
        _projectPaused = project['status'] as String? == 'paused';
        _messages
          ..clear()
          ..addAll(lines);
        _loading = false;
      });
      _scrollToEnd();
      await _loadInbox();
      await _openPendingApprovalsIfAny();
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  Future<void> _send() async {
    final text = _composer.text.trim();
    if (_sending || _chatBlocked) return;
    if (text.isEmpty && _pendingAttachments.isEmpty) return;

    final attachmentRefs = _pendingAttachments.map((a) => a.ref).toList(growable: false);
    final attachNames = _pendingAttachments.map((a) => a.filename).toList(growable: false);
    final sendText = text.isEmpty && attachNames.isNotEmpty
        ? '(attachment: ${attachNames.join(", ")})'
        : text;
    final displayText = sendText;

    setState(() {
      _sending = true;
      _cancelRequested = false;
      _error = null;
      _messages.add(
        _ChatLine(
          role: 'user',
          text: displayText,
          attachmentRefs: attachmentRefs,
        ),
      );
      _messages.add(_ChatLine(role: 'assistant', text: '', streaming: true));
      _composer.clear();
      _pendingAttachments.clear();
    });
    _scrollToEnd();

    var assistantIndex = _messages.length - 1;
    var assistantText = '';

    final handle = workContext.api.projectChatStream(
      projectId: widget.projectId,
      text: sendText,
      sessionId: _sessionId,
      attachmentRefs: attachmentRefs,
    );
    _activeStream = handle;

    try {
      await for (final event in handle.stream) {
        if (!mounted || _cancelRequested) break;
        final type = event['type'] as String?;
        final data = event['data'];
        final payload = data is Map<String, dynamic> ? data : const <String, dynamic>{};

        if (type == '_session') {
          setState(() => _sessionId = payload['session_id'] as String? ?? _sessionId);
        } else if (type == 'text_delta') {
          final chunk = payload['text'] as String? ?? '';
          if (chunk.isEmpty) continue;
          assistantText += chunk;
          setState(() {
            _messages[assistantIndex] = _ChatLine(
              role: 'assistant',
              text: assistantText,
              streaming: true,
            );
          });
          _scrollToEnd();
        } else if (type == 'tool_call') {
          final name = payload['name'] as String? ?? 'tool';
          setState(() {
            _messages.insert(
              assistantIndex,
              _ChatLine(role: 'tool', text: name),
            );
            assistantIndex += 1;
          });
          _scrollToEnd();
        } else if (type == 'tool_approval_request') {
          final name = payload['name'] as String? ?? 'tool';
          final approvalId = payload['id'] as String? ?? '';
          final input = payload['input'];
          setState(() {
            _messages.insert(
              assistantIndex,
              _ChatLine(
                role: 'approval',
                text: 'Approve $name?',
                approvalId: approvalId,
                toolName: name,
                toolInput: input is Map<String, dynamic> ? input : null,
              ),
            );
            assistantIndex += 1;
          });
          _scrollToEnd();
        } else if (type == '_turn_complete') {
          final finalText = payload['assistant_text'] as String? ?? assistantText;
          final pending = payload['pending_approvals'];
          setState(() {
            _sessionId = payload['session_id'] as String? ?? _sessionId;
            _messages[assistantIndex] = _ChatLine(role: 'assistant', text: finalText);
            _sending = false;
          });
          _scrollToEnd();
          if (pending is List && pending.isNotEmpty) {
            final first = pending.first;
            if (first is Map<String, dynamic>) {
              await _openApproval(
                approvalId: first['id'] as String? ?? '',
                toolName: first['name'] as String? ?? 'tool',
                toolInput: first['input'] is Map<String, dynamic>
                    ? first['input'] as Map<String, dynamic>
                    : const {},
              );
            }
          }
        } else if (type == '_error') {
          final detail = payload['detail'] as String? ?? payload['code'] as String? ?? 'Agent error';
          setState(() {
            _error = detail;
            if (assistantText.isEmpty && _messages.length > assistantIndex) {
              _messages.removeAt(assistantIndex);
            } else {
              _messages[assistantIndex] = _ChatLine(role: 'assistant', text: assistantText);
            }
            _sending = false;
          });
        }
      }
      if (!mounted) return;
      if (_cancelRequested) {
        setState(() {
          _messages[assistantIndex] = _ChatLine(
            role: 'assistant',
            text: assistantText.isEmpty ? '(cancelled)' : '$assistantText\n(cancelled)',
          );
          _sending = false;
          _cancelRequested = false;
        });
        return;
      }
      setState(() => _sending = false);
    } catch (e) {
      if (!mounted) return;
      if (_cancelRequested) {
        setState(() {
          _messages[assistantIndex] = _ChatLine(
            role: 'assistant',
            text: assistantText.isEmpty ? '(cancelled)' : '$assistantText\n(cancelled)',
          );
          _sending = false;
          _cancelRequested = false;
          _error = null;
        });
        return;
      }
      setState(() {
        _error = e.toString();
        if (assistantText.isEmpty && _messages.length > assistantIndex) {
          _messages.removeAt(assistantIndex);
        }
        _sending = false;
      });
    } finally {
      _activeStream = null;
    }
  }

  Future<void> _cancelStream() async {
    if (!_sending) return;
    setState(() => _cancelRequested = true);
    _activeStream?.abort();
    final sid = _sessionId;
    if (sid == null) return;
    try {
      await workContext.api.cancelAgentSession(projectId: widget.projectId, sessionId: sid);
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = e.toString());
    }
  }

  Future<void> _openPendingApprovalsIfAny() async {
    final sid = _sessionId;
    if (sid == null) return;
    try {
      final pending = await workContext.api.listPendingApprovals(
        projectId: widget.projectId,
        sessionId: sid,
      );
      if (!mounted || pending.isEmpty) return;
      final first = pending.first;
      await _openApproval(
        approvalId: first['id'] as String? ?? '',
        toolName: first['name'] as String? ?? 'tool',
        toolInput: first['input'] is Map<String, dynamic>
            ? first['input'] as Map<String, dynamic>
            : const {},
      );
    } catch (_) {
      // Non-fatal on reload; user can tap approval bubble.
    }
  }

  Future<void> _openApproval({
    required String approvalId,
    required String toolName,
    Map<String, dynamic> toolInput = const {},
  }) async {
    final sid = _sessionId;
    if (sid == null || approvalId.isEmpty) return;
    final decision = await Navigator.of(context).push<String>(
      MaterialPageRoute(
        builder: (_) => ToolApprovePage(
          projectId: widget.projectId,
          sessionId: sid,
          approvalId: approvalId,
          toolName: toolName,
          toolInput: toolInput,
        ),
      ),
    );
    if (!mounted || decision == null) return;
    await _loadTranscript();
  }

  void _scrollToEnd() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scroll.hasClients) return;
      _scroll.animateTo(
        _scroll.position.maxScrollExtent,
        duration: const Duration(milliseconds: 200),
        curve: Curves.easeOut,
      );
    });
  }

  @override
  Widget build(BuildContext context) {
    return AppScaffold(
      title: Text(_projectName),
      actions: [
        IconButton(
          onPressed: _loading || _sending
              ? null
              : () async {
                  final updated = await Navigator.of(context).push<String>(
                    MaterialPageRoute(
                      builder: (_) => ProjectSettingsPage(
                        projectId: widget.projectId,
                        projectName: _projectName,
                      ),
                    ),
                  );
                  if (!mounted) return;
                  await _loadTranscript();
                  if (updated != null) {
                    setState(() => _projectName = updated);
                  }
                },
          icon: const Icon(Icons.settings_outlined),
          tooltip: 'Project settings',
        ),
        IconButton(
          onPressed: _loading || _sending ? null : _loadTranscript,
          icon: const Icon(Icons.refresh),
          tooltip: 'Reload transcript',
        ),
      ],
      body: Column(
        children: [
          if (_companySuspended)
            MaterialBanner(
              content: const Text('Company subscription expired — chat and uploads are disabled'),
              leading: const Icon(Icons.pause_circle_outline),
              backgroundColor: Theme.of(context).colorScheme.errorContainer,
              actions: const [SizedBox.shrink()],
            ),
          if (_projectPaused && !_companySuspended)
            MaterialBanner(
              content: const Text('Project is paused — chat and uploads are disabled'),
              leading: const Icon(Icons.pause_circle_filled),
              backgroundColor: Theme.of(context).colorScheme.secondaryContainer,
              actions: const [SizedBox.shrink()],
            ),
          if (_error != null) InlineErrorBanner(message: _error!),
          if (_inboxAttachments.isNotEmpty)
            ExpansionTile(
              initiallyExpanded: false,
              title: Text('Inbox (${_inboxAttachments.length})'),
              leading: const Icon(Icons.folder_open_outlined, size: 20),
              children: [
                for (final item in _inboxAttachments)
                  ListTile(
                    dense: true,
                    title: Text(
                      (item['filename'] as String?) ?? 'file',
                      overflow: TextOverflow.ellipsis,
                    ),
                    subtitle: Text(
                      '${item['size_bytes'] ?? '?'} B',
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                    trailing: IconButton(
                      icon: const Icon(Icons.delete_outline, size: 20),
                      tooltip: 'Delete',
                      onPressed: _sending || _chatBlocked
                          ? null
                          : () => _deleteInboxAttachment(item),
                    ),
                  ),
              ],
            ),
          Expanded(
            child: _loading
                ? const Center(child: CircularProgressIndicator())
                : _messages.isEmpty
                    ? const Center(
                        child: Text('Send a message to start the agent session'),
                      )
                    : ListView.builder(
                        controller: _scroll,
                        padding: const EdgeInsets.all(12),
                        itemCount: _messages.length,
                        itemBuilder: (context, index) {
                          final msg = _messages[index];
                          final isUser = msg.role == 'user';
                          final isTool = msg.role == 'tool';
                          final isApproval = msg.role == 'approval';
                          return Align(
                            alignment: isUser
                                ? Alignment.centerRight
                                : isTool || isApproval
                                    ? Alignment.center
                                    : Alignment.centerLeft,
                            child: InkWell(
                              onTap: isApproval && msg.approvalId != null
                                  ? () => _openApproval(
                                        approvalId: msg.approvalId!,
                                        toolName: msg.toolName ?? 'tool',
                                        toolInput: msg.toolInput ?? const {},
                                      )
                                  : null,
                              child: Container(
                              margin: const EdgeInsets.only(bottom: 8),
                              padding: EdgeInsets.symmetric(
                                horizontal: isTool || isApproval ? 10 : 12,
                                vertical: isTool || isApproval ? 4 : 8,
                              ),
                              constraints: BoxConstraints(
                                maxWidth: MediaQuery.sizeOf(context).width *
                                    (isTool || isApproval ? 0.9 : 0.82),
                              ),
                              decoration: BoxDecoration(
                                color: isApproval
                                    ? Theme.of(context).colorScheme.errorContainer
                                    : isTool
                                        ? Theme.of(context).colorScheme.surfaceContainerLow
                                        : isUser
                                            ? Theme.of(context).colorScheme.primaryContainer
                                            : Theme.of(context).colorScheme.surfaceContainerHighest,
                                borderRadius: BorderRadius.circular(isTool || isApproval ? 8 : 12),
                              ),
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                mainAxisSize: MainAxisSize.min,
                                children: [
                                  Row(
                                    mainAxisSize: MainAxisSize.min,
                                    children: [
                                      if (isTool || isApproval) ...[
                                        Icon(
                                          isApproval ? Icons.gavel_outlined : Icons.build_outlined,
                                          size: 14,
                                          color: Theme.of(context).colorScheme.onSurfaceVariant,
                                        ),
                                        const SizedBox(width: 4),
                                      ],
                                      Flexible(
                                        child: Text(
                                          isTool || isApproval
                                              ? msg.text
                                              : msg.text.isEmpty && msg.streaming
                                                  ? '…'
                                                  : msg.text,
                                          style: isTool || isApproval
                                              ? Theme.of(context).textTheme.labelSmall?.copyWith(
                                                    color: Theme.of(context).colorScheme.onSurfaceVariant,
                                                  )
                                              : null,
                                        ),
                                      ),
                                      if (msg.streaming) ...[
                                        const SizedBox(width: 6),
                                        const SizedBox(
                                          width: 12,
                                          height: 12,
                                          child: CircularProgressIndicator(strokeWidth: 2),
                                        ),
                                      ],
                                    ],
                                  ),
                                  if (isUser && msg.attachmentRefs.isNotEmpty) ...[
                                    const SizedBox(height: 6),
                                    Wrap(
                                      spacing: 4,
                                      runSpacing: 4,
                                      children: [
                                        for (final ref in msg.attachmentRefs)
                                          Chip(
                                            visualDensity: VisualDensity.compact,
                                            avatar: const Icon(Icons.attach_file, size: 14),
                                            label: Text(
                                              _attachmentLabel(ref),
                                              overflow: TextOverflow.ellipsis,
                                            ),
                                          ),
                                      ],
                                    ),
                                  ],
                                ],
                              ),
                            ),
                            ),
                          );
                        },
                      ),
          ),
          SafeArea(
            top: false,
            child: Padding(
              padding: const EdgeInsets.fromLTRB(12, 0, 12, 12),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  if (_pendingAttachments.isNotEmpty)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: Wrap(
                        spacing: 6,
                        runSpacing: 6,
                        children: [
                          for (final att in _pendingAttachments)
                            InputChip(
                              label: Text(att.filename, overflow: TextOverflow.ellipsis),
                              onDeleted: _sending ? null : () { _removeAttachment(att); },
                            ),
                        ],
                      ),
                    ),
                  Row(
                    children: [
                      IconButton(
                        onPressed: _loading || _sending || _uploadingAttachment || _chatBlocked
                            ? null
                            : _pickAttachment,
                        icon: _uploadingAttachment
                            ? const SizedBox(
                                width: 20,
                                height: 20,
                                child: CircularProgressIndicator(strokeWidth: 2),
                              )
                            : const Icon(Icons.attach_file),
                        tooltip: 'Attach file',
                      ),
                      Expanded(
                        child: TextField(
                          controller: _composer,
                          minLines: 1,
                          maxLines: 4,
                          enabled: !_loading && !_chatBlocked,
                          textInputAction: TextInputAction.send,
                          onSubmitted: (_) => _send(),
                          decoration: const InputDecoration(
                            hintText: 'Message…',
                            border: OutlineInputBorder(),
                            isDense: true,
                          ),
                        ),
                      ),
                      const SizedBox(width: 8),
                      if (_sending)
                        IconButton(
                          onPressed: _cancelStream,
                          icon: const Icon(Icons.stop_circle_outlined),
                          tooltip: 'Cancel',
                        ),
                      IconButton.filled(
                        onPressed: _sending || _loading || _chatBlocked ? null : _send,
                        icon: _sending
                            ? const SizedBox(
                                width: 18,
                                height: 18,
                                child: CircularProgressIndicator(strokeWidth: 2),
                              )
                            : const Icon(Icons.send),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}
