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
  String? _sessionId;
  final _messages = <String>[];
  bool _starting = false;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _ensureSession() async {
    if (_sessionId != null || _starting) return;
    setState(() => _starting = true);
    try {
      final data = await AppScope.of(context).startAgentSession(widget.projectId);
      _sessionId = data['id'] as String?;
      final tools = (data['allowed_tools'] as List<dynamic>? ?? []).join(', ');
      _messages.add('Сессия ${data['pack_id']} · tools: ${tools.isEmpty ? "нет" : tools}');
    } finally {
      if (mounted) setState(() => _starting = false);
    }
  }

  Future<void> _send() async {
    final text = _controller.text.trim();
    if (text.isEmpty) return;
    await _ensureSession();
    final sid = _sessionId;
    if (sid == null || !mounted) return;
    _controller.clear();
    setState(() => _messages.add('Вы: $text'));
    final data = await AppScope.of(context).sendAgentMessage(
      projectId: widget.projectId,
      sessionId: sid,
      text: text,
    );
    final msg = data['message'] as Map<String, dynamic>?;
    if (mounted && msg != null) {
      setState(() => _messages.add(msg['text'] as String? ?? ''));
    }
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text('Агент (platform)', style: Theme.of(context).textTheme.titleMedium),
        const SizedBox(height: AppSpacing.sm),
        Text(
          'Чат и промпты — слой платформы; доменные tools регистрирует кабинет.',
          style: Theme.of(context).textTheme.bodySmall,
        ),
        const SizedBox(height: AppSpacing.md),
        Expanded(
          child: ListView.builder(
            itemCount: _messages.length,
            itemBuilder: (_, i) => Padding(
              padding: const EdgeInsets.only(bottom: AppSpacing.sm),
              child: Text(_messages[i]),
            ),
          ),
        ),
        Row(
          children: [
            Expanded(
              child: AppTextField(
                controller: _controller,
                label: 'Сообщение',
              ),
            ),
            const SizedBox(width: AppSpacing.sm),
            AppButton(
              label: _starting ? '…' : 'Отправить',
              expanded: false,
              onPressed: _starting ? null : _send,
            ),
          ],
        ),
      ],
    );
  }
}
