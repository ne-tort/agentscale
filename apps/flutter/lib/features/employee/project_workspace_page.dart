import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';

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
  });

  final String role;
  final String text;
  final bool streaming;
}

class _PendingAttachment {
  const _PendingAttachment({required this.ref, required this.filename});

  final String ref;
  final String filename;
}

class _ProjectWorkspacePageState extends State<ProjectWorkspacePage> {
  final _composer = TextEditingController();
  final _scroll = ScrollController();
  final _messages = <_ChatLine>[];
  final _pendingAttachments = <_PendingAttachment>[];
  String? _sessionId;
  bool _loading = true;
  bool _sending = false;
  bool _uploadingAttachment = false;
  bool _cancelRequested = false;
  String? _error;
  ProjectChatStreamHandle? _activeStream;

  @override
  void initState() {
    super.initState();
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
    if (_uploadingAttachment || _sending || _loading) return;
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
      if (ref == null || ref.isEmpty) {
        setState(() => _error = 'Upload missing storage_ref');
        return;
      }
      setState(() {
        _pendingAttachments.add(_PendingAttachment(ref: ref, filename: filename));
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _uploadingAttachment = false);
    }
  }

  void _removeAttachment(_PendingAttachment item) {
    setState(() => _pendingAttachments.remove(item));
  }

  Future<void> _loadTranscript() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final result = await workContext.api.projectChatTranscript(projectId: widget.projectId);
      if (!mounted) return;
      final items = result['messages'];
      final lines = <_ChatLine>[];
      if (items is List) {
        for (final raw in items) {
          if (raw is! Map) continue;
          final role = raw['role'] as String? ?? 'assistant';
          final text = raw['text'] as String? ?? '';
          if (text.isEmpty) continue;
          lines.add(_ChatLine(role: role, text: text));
        }
      }
      setState(() {
        _sessionId = result['session_id'] as String?;
        _messages
          ..clear()
          ..addAll(lines);
        _loading = false;
      });
      _scrollToEnd();
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
    if (text.isEmpty || _sending) return;

    final attachmentRefs = _pendingAttachments.map((a) => a.ref).toList(growable: false);

    setState(() {
      _sending = true;
      _cancelRequested = false;
      _error = null;
      _messages.add(_ChatLine(role: 'user', text: text));
      _messages.add(_ChatLine(role: 'assistant', text: '', streaming: true));
      _composer.clear();
      _pendingAttachments.clear();
    });
    _scrollToEnd();

    var assistantIndex = _messages.length - 1;
    var assistantText = '';

    final handle = workContext.api.projectChatStream(
      projectId: widget.projectId,
      text: text,
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
        } else if (type == '_turn_complete') {
          final finalText = payload['assistant_text'] as String? ?? assistantText;
          setState(() {
            _sessionId = payload['session_id'] as String? ?? _sessionId;
            _messages[assistantIndex] = _ChatLine(role: 'assistant', text: finalText);
            _sending = false;
          });
          _scrollToEnd();
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
        setState(() => _sending = false);
        return;
      }
      setState(() => _sending = false);
    } catch (e) {
      if (!mounted) return;
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
      title: Text(widget.projectName),
      actions: [
        IconButton(
          onPressed: _loading || _sending ? null : _loadTranscript,
          icon: const Icon(Icons.refresh),
          tooltip: 'Reload transcript',
        ),
      ],
      body: Column(
        children: [
          if (_error != null) InlineErrorBanner(message: _error!),
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
                          return Align(
                            alignment: isUser
                                ? Alignment.centerRight
                                : isTool
                                    ? Alignment.center
                                    : Alignment.centerLeft,
                            child: Container(
                              margin: const EdgeInsets.only(bottom: 8),
                              padding: EdgeInsets.symmetric(
                                horizontal: isTool ? 10 : 12,
                                vertical: isTool ? 4 : 8,
                              ),
                              constraints: BoxConstraints(
                                maxWidth: MediaQuery.sizeOf(context).width * (isTool ? 0.9 : 0.82),
                              ),
                              decoration: BoxDecoration(
                                color: isTool
                                    ? Theme.of(context).colorScheme.surfaceContainerLow
                                    : isUser
                                        ? Theme.of(context).colorScheme.primaryContainer
                                        : Theme.of(context).colorScheme.surfaceContainerHighest,
                                borderRadius: BorderRadius.circular(isTool ? 8 : 12),
                              ),
                              child: Row(
                                mainAxisSize: MainAxisSize.min,
                                children: [
                                  if (isTool) ...[
                                    Icon(
                                      Icons.build_outlined,
                                      size: 14,
                                      color: Theme.of(context).colorScheme.onSurfaceVariant,
                                    ),
                                    const SizedBox(width: 4),
                                  ],
                                  Flexible(
                                    child: Text(
                                      isTool
                                          ? msg.text
                                          : msg.text.isEmpty && msg.streaming
                                              ? '…'
                                              : msg.text,
                                      style: isTool
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
                              onDeleted: _sending ? null : () => _removeAttachment(att),
                            ),
                        ],
                      ),
                    ),
                  Row(
                    children: [
                      IconButton(
                        onPressed: _loading || _sending || _uploadingAttachment ? null : _pickAttachment,
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
                          enabled: !_loading,
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
                        onPressed: _sending || _loading ? null : _send,
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
