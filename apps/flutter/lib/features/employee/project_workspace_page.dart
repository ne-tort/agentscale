import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/refresh/app_auto_refresh.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';
import 'package:prodavan/features/employee/project_settings_page.dart';
import 'package:prodavan/features/employee/tool_approve_page.dart';
import 'package:prodavan/features/employee/widgets/attachment_image_viewer.dart';
import 'package:prodavan/features/employee/widgets/attachment_preview_chip.dart';
import 'package:prodavan/features/employee/widgets/project_status_banner.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/l10n/app_localizations.dart';

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
  late final AppAutoRefreshBinder _autoRefresh;
  bool _loading = true;
  bool _sending = false;
  bool _uploadingAttachment = false;
  bool _cancelRequested = false;
  bool _companySuspended = false;
  bool _projectPaused = false;
  bool _resuming = false;
  String? _error;
  ProjectChatStreamHandle? _activeStream;

  bool get _chatBlocked => _companySuspended || _projectPaused;
  /// Inbox cleanup allowed while paused; blocked only when company suspended.
  bool get _inboxMutationsBlocked => _companySuspended;

  List<Map<String, dynamic>> _messagesSnapshot(List<_ChatLine> lines) {
    return [
      for (final m in lines)
        {
          'role': m.role,
          'text': m.text,
          'streaming': m.streaming,
          'approvalId': m.approvalId,
          'toolName': m.toolName,
          'toolInput': m.toolInput,
          'attachmentRefs': m.attachmentRefs,
        },
    ];
  }

  Future<void> _resumeFromBanner() async {
    if (_resuming || !_projectPaused || _companySuspended) return;
    setState(() {
      _resuming = true;
      _error = null;
    });
    try {
      await workContext.api.resumeProject(widget.projectId);
      if (!mounted) return;
      setState(() => _projectPaused = false);
      await _loadTranscript();
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _resuming = false);
    }
  }

  @override
  void initState() {
    super.initState();
    _projectName = widget.projectName;
    workContext.enterProject(widget.projectId);
    _autoRefresh = AppAutoRefreshBinder(
      onTick: () async {
        if (_sending || _activeStream != null) return;
        await _loadTranscript(silent: true);
      },
      isActive: () => appAutoRefreshIsActive(context),
    )..attach();
    _loadTranscript();
  }

  @override
  void dispose() {
    _autoRefresh.dispose();
    _activeStream?.abort();
    _composer.dispose();
    _scroll.dispose();
    super.dispose();
  }

  Future<void> _pickAttachment() async {
    final l10n = AppLocalizations.of(context);
    if (_uploadingAttachment || _sending || _loading || _chatBlocked) return;
    final result = await FilePicker.platform.pickFiles(withData: true);
    if (result == null || result.files.isEmpty) return;
    final file = result.files.first;
    final bytes = file.bytes;
    if (bytes == null) {
      if (!mounted) return;
      setState(() => _error = l10n.projectCouldNotReadFileBytes);
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
        setState(() => _error = l10n.projectUploadMissingId);
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
    final meta = _metaForRef(ref);
    final name = meta?['filename'] as String?;
    if (name != null && name.isNotEmpty) return name;
    final slash = ref.replaceAll('\\', '/').lastIndexOf('/');
    if (slash >= 0 && slash < ref.length - 1) {
      return ref.substring(slash + 1);
    }
    return ref.length > 24 ? '${ref.substring(0, 21)}…' : ref;
  }

  Map<String, dynamic>? _metaForRef(String ref) {
    for (final item in _inboxAttachments) {
      if (item['storage_ref'] == ref || item['id'] == ref) {
        return item;
      }
    }
    return null;
  }

  Widget _attachmentChip(String ref) {
    final meta = _metaForRef(ref);
    final id = meta?['id'] as String?;
    if (id != null && id.isNotEmpty) {
      return AttachmentPreviewChip(
        projectId: widget.projectId,
        attachmentId: id,
        label: _attachmentLabel(ref),
        contentType: meta?['content_type'] as String?,
      );
    }
    return Chip(
      visualDensity: VisualDensity.compact,
      avatar: const Icon(Icons.attach_file, size: 14),
      label: Text(
        _attachmentLabel(ref),
        overflow: TextOverflow.ellipsis,
      ),
    );
  }

  Future<void> _openAttachmentPreview(Map<String, dynamic> item) {
    final id = item['id'] as String?;
    if (id == null || id.isEmpty) return Future.value();
    return AttachmentViewerPage.openIfPreviewable(
      context,
      projectId: widget.projectId,
      attachmentId: id,
      title: (item['filename'] as String?) ?? 'attachment',
      contentType: item['content_type'] as String?,
    );
  }

  Widget? _inboxLeading(Map<String, dynamic> item) {
    final id = item['id'] as String?;
    final contentType = item['content_type'] as String?;
    if (id == null || id.isEmpty) {
      return const Icon(Icons.insert_drive_file_outlined, size: 20);
    }
    return AttachmentThumbnail(
      projectId: widget.projectId,
      attachmentId: id,
      contentType: contentType,
      size: 40,
      onTap: AttachmentPreviewChip.canPreview(contentType)
          ? () => _openAttachmentPreview(item)
          : null,
    );
  }

  Future<void> _loadTranscript({bool silent = false}) async {
    if (!silent && mounted) {
      setState(() {
        _loading = true;
        _error = null;
      });
    }
    try {
      final project = await workContext.api.getProject(widget.projectId);
      final sub = project['company_subscription'] as Map<String, dynamic>? ?? const {};
      final result = await workContext.api.projectChatTranscript(projectId: widget.projectId);
      if (!mounted) return;
      // Avoid overwriting an in-progress stream that started while we were fetching.
      if (silent && (_sending || _activeStream != null)) return;
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
      final status = result['session_status'] as String?;
      final nextSessionId = status == 'active' ? result['session_id'] as String? : null;
      final nextSuspended = sub['subscription_expired'] == true;
      final nextPaused = (project['status'] as String?) == 'paused';
      if (silent &&
          appRefreshDataEquals(_messagesSnapshot(_messages), _messagesSnapshot(lines)) &&
          _sessionId == nextSessionId &&
          _companySuspended == nextSuspended &&
          _projectPaused == nextPaused &&
          !_loading) {
        return;
      }
      setState(() {
        // Only keep sendable session; cancelled leftovers after pause must not be reused.
        _sessionId = nextSessionId;
        _companySuspended = nextSuspended;
        _projectPaused = nextPaused;
        _messages
          ..clear()
          ..addAll(lines);
        _loading = false;
        if (!silent) _error = null;
      });
      if (_projectPaused || _companySuspended) {
        await _abortLocalStreamIfSending();
      }
      if (!silent) _scrollToEnd();
      await _loadInbox();
      if (!silent) await _openPendingApprovalsIfAny();
    } catch (e) {
      if (!mounted) return;
      if (silent) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  Future<void> _regenerateLast() async {
    if (_sending || _chatBlocked) return;
    for (var i = _messages.length - 1; i >= 0; i--) {
      final msg = _messages[i];
      if (msg.role != 'user') continue;
      final text = msg.text.trim();
      if (text.isEmpty || text == '(attachment)') return;
      _composer.text = text;
      await _send();
      return;
    }
  }

  Future<void> _send() async {
    final l10n = AppLocalizations.of(context);
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
                text: l10n.projectApproveToolPrompt(name),
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
          final detail = payload['detail'] as String? ?? payload['code'] as String? ?? l10n.projectAgentError;
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
            text: assistantText.isEmpty
                ? l10n.projectCancelledMarker
                : l10n.projectCancelledWithText(assistantText),
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
            text: assistantText.isEmpty
                ? l10n.projectCancelledMarker
                : l10n.projectCancelledWithText(assistantText),
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

  /// Local SSE abort when project becomes paused/suspended mid-stream (settings / reload).
  Future<void> _abortLocalStreamIfSending() async {
    if (!_sending) return;
    setState(() => _cancelRequested = true);
    _activeStream?.abort();
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
    if (_chatBlocked) return;
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
    final l10n = AppLocalizations.of(context);
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
          icon: Icon(Icons.settings_outlined),
          tooltip: l10n.projectProjectSettings,
        ),
      ],
      body: Column(
        children: [
          ...ProjectStatusBanner.build(
            context,
            companySuspended: _companySuspended,
            projectPaused: _projectPaused,
            onResume: _projectPaused && !_companySuspended && !_resuming
                ? _resumeFromBanner
                : null,
          ),
          if (_error != null) InlineErrorBanner(message: _error!),
          if (_inboxAttachments.isNotEmpty)
            ExpansionTile(
              initiallyExpanded: false,
              title: Text(l10n.projectInbox('${_inboxAttachments.length}')),
              leading: const Icon(Icons.folder_open_outlined, size: 20),
              children: [
                for (final item in _inboxAttachments)
                  ListTile(
                    dense: true,
                    leading: _inboxLeading(item),
                    title: Text(
                      (item['filename'] as String?) ?? 'file',
                      overflow: TextOverflow.ellipsis,
                    ),
                    subtitle: Text(
                      '${item['size_bytes'] ?? '?'} B',
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                    onTap: AttachmentPreviewChip.canPreview(
                      item['content_type'] as String?,
                    )
                        ? () => _openAttachmentPreview(item)
                        : null,
                    trailing: IconButton(
                      icon: Icon(Icons.delete_outline, size: 20),
                      tooltip: l10n.commonDelete,
                      onPressed: _sending || _inboxMutationsBlocked
                          ? null
                          : () => _deleteInboxAttachment(item),
                    ),
                  ),
              ],
            ),
          Expanded(
            child: _loading
                ? Center(child: CircularProgressIndicator())
                : _messages.isEmpty
                    ? EmptyPlaceholder(
                        title: l10n.projectEmptyChatHint,
                        icon: Icons.chat_bubble_outline,
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
                              onTap: isApproval &&
                                      msg.approvalId != null &&
                                      !_chatBlocked
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
                                    ? context.appColors.dangerContainer
                                    : isTool
                                        ? context.appColors.surfaceContainer
                                        : isUser
                                            ? context.appColors.primaryContainer
                                            : context.appColors.surfaceContainer,
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
                                          color: context.appColors.muted,
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
                                                    color: context.appColors.muted,
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
                                      if (!isUser &&
                                          !isTool &&
                                          !isApproval &&
                                          !msg.streaming &&
                                          index == _messages.length - 1 &&
                                          !_sending &&
                                          !_chatBlocked) ...[
                                        const SizedBox(width: 4),
                                        IconButton(
                                          visualDensity: VisualDensity.compact,
                                          padding: EdgeInsets.zero,
                                          constraints: BoxConstraints(minWidth: 28, minHeight: 28),
                                          tooltip: l10n.projectRegenerate,
                                          icon: const Icon(Icons.refresh, size: 16),
                                          onPressed: _regenerateLast,
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
                                          _attachmentChip(ref),
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
                            ? SizedBox(
                                width: 20,
                                height: 20,
                                child: CircularProgressIndicator(strokeWidth: 2),
                              )
                            : Icon(Icons.attach_file),
                        tooltip: l10n.projectAttachFile,
                      ),
                      Expanded(
                        child: TextField(
                          controller: _composer,
                          minLines: 1,
                          maxLines: 4,
                          enabled: !_loading && !_chatBlocked,
                          textInputAction: TextInputAction.send,
                          onSubmitted: (_) => _send(),
                          decoration: InputDecoration(
                            hintText: l10n.projectMessageHint,
                            border: OutlineInputBorder(),
                            isDense: true,
                          ),
                        ),
                      ),
                      const SizedBox(width: 8),
                      if (_sending)
                        IconButton(
                          onPressed: _cancelStream,
                          icon: Icon(Icons.stop_circle_outlined),
                          tooltip: l10n.commonCancel,
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
