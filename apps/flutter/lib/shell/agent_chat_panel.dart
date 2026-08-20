import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/widgets.dart';
import 'package:prodavan/shell/app_scope.dart';

/// Platform agent chat tab (tools list comes from cabinet pack via session).
class AgentChatPanel extends StatefulWidget {
  const AgentChatPanel({super.key, required this.projectId});

  final String projectId;

  @override
  State<AgentChatPanel> createState() => _AgentChatPanelState();
}

class _AgentChatPanelState extends State<AgentChatPanel> {
  final _controller = TextEditingController();
  final _scroll = ScrollController();
  String? _sessionId;
  final _messages = <_ChatLine>[];
  bool _starting = false;
  bool _sending = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _ensureSession());
  }

  @override
  void dispose() {
    _controller.dispose();
    _scroll.dispose();
    super.dispose();
  }

  Future<void> _ensureSession() async {
    if (_sessionId != null || _starting || !mounted) return;
    setState(() {
      _starting = true;
      _error = null;
    });
    try {
      final data = await AppScope.of(context).startAgentSession(widget.projectId);
      if (!mounted) return;
      _sessionId = data['id'] as String?;
      final tools = (data['allowed_tools'] as List<dynamic>? ?? []).cast<Object?>();
      final toolsLabel = tools.isEmpty ? 'без доменных tools' : tools.join(', ');
      setState(() {
        _messages.add(
          _ChatLine.system(
            'Сессия ${data['pack_id'] ?? 'agent'} · $toolsLabel',
          ),
        );
      });
    } catch (e) {
      if (mounted) setState(() => _error = 'Не удалось открыть сессию агента');
    } finally {
      if (mounted) setState(() => _starting = false);
    }
  }

  Future<void> _send() async {
    final text = _controller.text.trim();
    if (text.isEmpty || _sending) return;
    await _ensureSession();
    final sid = _sessionId;
    if (sid == null || !mounted) return;
    _controller.clear();
    setState(() {
      _sending = true;
      _messages.add(_ChatLine.user(text));
      _error = null;
    });
    _scrollToEnd();
    try {
      final data = await AppScope.of(context).sendAgentMessage(
        projectId: widget.projectId,
        sessionId: sid,
        text: text,
      );
      final msg = data['message'] as Map<String, dynamic>?;
      if (mounted && msg != null) {
        setState(() {
          _messages.add(_ChatLine.agent(msg['text'] as String? ?? ''));
        });
        _scrollToEnd();
      }
    } catch (_) {
      if (mounted) setState(() => _error = 'Сообщение не отправлено');
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  void _scrollToEnd() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scroll.hasClients) return;
      _scroll.animateTo(
        _scroll.position.maxScrollExtent + 80,
        duration: const Duration(milliseconds: 200),
        curve: Curves.easeOut,
      );
    });
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text('Агент', style: theme.textTheme.titleMedium),
        const SizedBox(height: AppSpacing.xs),
        Text(
          'Чат и промпты — слой платформы; tools регистрирует кабинет.',
          style: theme.textTheme.bodySmall,
        ),
        if (_error != null) ...[
          const SizedBox(height: AppSpacing.sm),
          Text(_error!, style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.error)),
        ],
        const SizedBox(height: AppSpacing.md),
        Expanded(
          child: _starting && _messages.isEmpty
              ? const Center(child: CircularProgressIndicator())
              : ListView.builder(
                  controller: _scroll,
                  itemCount: _messages.length,
                  itemBuilder: (_, i) => _MessageBubble(line: _messages[i]),
                ),
        ),
        const SizedBox(height: AppSpacing.sm),
        Row(
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            Expanded(
              child: AppTextField(
                controller: _controller,
                label: 'Сообщение',
                textInputAction: TextInputAction.send,
                onFieldSubmitted: (_) => _send(),
              ),
            ),
            const SizedBox(width: AppSpacing.sm),
            AppButton(
              label: _sending ? '…' : 'Отправить',
              expanded: false,
              onPressed: (_starting || _sending) ? null : _send,
            ),
          ],
        ),
      ],
    );
  }
}

class _ChatLine {
  const _ChatLine._(this.role, this.text);

  factory _ChatLine.user(String text) => _ChatLine._(_Role.user, text);
  factory _ChatLine.agent(String text) => _ChatLine._(_Role.agent, text);
  factory _ChatLine.system(String text) => _ChatLine._(_Role.system, text);

  final _Role role;
  final String text;
}

enum _Role { user, agent, system }

class _MessageBubble extends StatelessWidget {
  const _MessageBubble({required this.line});

  final _ChatLine line;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final align = line.role == _Role.user ? Alignment.centerRight : Alignment.centerLeft;
    final bg = switch (line.role) {
      _Role.user => theme.colorScheme.primaryContainer,
      _Role.agent => theme.colorScheme.surfaceContainerHighest,
      _Role.system => theme.colorScheme.surface,
    };
    final fg = switch (line.role) {
      _Role.user => theme.colorScheme.onPrimaryContainer,
      _Role.agent => theme.colorScheme.onSurface,
      _Role.system => theme.colorScheme.onSurfaceVariant,
    };
    return Align(
      alignment: align,
      child: Container(
        margin: const EdgeInsets.only(bottom: AppSpacing.sm),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        constraints: BoxConstraints(maxWidth: MediaQuery.sizeOf(context).width * 0.85),
        decoration: BoxDecoration(
          color: bg,
          borderRadius: BorderRadius.circular(12),
          border: line.role == _Role.system
              ? Border.all(color: theme.colorScheme.outlineVariant)
              : null,
        ),
        child: Text(line.text, style: theme.textTheme.bodyMedium?.copyWith(color: fg)),
      ),
    );
  }
}
