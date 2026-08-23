import 'package:flutter/material.dart';

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

class _ProjectWorkspacePageState extends State<ProjectWorkspacePage> {
  final _composer = TextEditingController();
  final _scroll = ScrollController();
  final _messages = <_ChatLine>[];
  String? _sessionId;
  bool _loading = true;
  bool _sending = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    workContext.enterProject(widget.projectId);
    _loadTranscript();
  }

  @override
  void dispose() {
    _composer.dispose();
    _scroll.dispose();
    super.dispose();
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

    setState(() {
      _sending = true;
      _error = null;
      _messages.add(_ChatLine(role: 'user', text: text));
      _messages.add(_ChatLine(role: 'assistant', text: '', streaming: true));
      _composer.clear();
    });
    _scrollToEnd();

    final assistantIndex = _messages.length - 1;
    var assistantText = '';

    try {
      await for (final event in workContext.api.projectChatStream(
        projectId: widget.projectId,
        text: text,
        sessionId: _sessionId,
      )) {
        if (!mounted) return;
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
                          return Align(
                            alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
                            child: Container(
                              margin: const EdgeInsets.only(bottom: 8),
                              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                              constraints: BoxConstraints(
                                maxWidth: MediaQuery.sizeOf(context).width * 0.82,
                              ),
                              decoration: BoxDecoration(
                                color: isUser
                                    ? Theme.of(context).colorScheme.primaryContainer
                                    : Theme.of(context).colorScheme.surfaceContainerHighest,
                                borderRadius: BorderRadius.circular(12),
                              ),
                              child: Row(
                                mainAxisSize: MainAxisSize.min,
                                children: [
                                  Flexible(child: Text(msg.text.isEmpty && msg.streaming ? '…' : msg.text)),
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
              child: Row(
                children: [
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
            ),
          ),
        ],
      ),
    );
  }
}
