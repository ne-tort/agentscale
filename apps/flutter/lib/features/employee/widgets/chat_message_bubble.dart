import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_spacing.dart';

class ChatMessageBubble extends StatelessWidget {
  const ChatMessageBubble({super.key, required this.message});

  final Map<String, dynamic> message;

  @override
  Widget build(BuildContext context) {
    final role = message['role'] as String? ?? 'assistant';
    final text = message['text'] as String? ?? '';
    final scheme = Theme.of(context).colorScheme;
    final isUser = role == 'user';
    final alignment = isUser ? Alignment.centerRight : Alignment.centerLeft;
    final Color bg = switch (role) {
      'user' => scheme.primaryContainer,
      'tool' || 'approval' => scheme.surfaceContainerHighest,
      _ => scheme.surfaceContainerLow,
    };
    return Align(
      alignment: alignment,
      child: Container(
        margin: EdgeInsets.only(bottom: AppSpacing.sm),
        padding: EdgeInsets.symmetric(horizontal: AppSpacing.md, vertical: AppSpacing.sm),
        constraints: BoxConstraints(maxWidth: MediaQuery.sizeOf(context).width * 0.85),
        decoration: BoxDecoration(
          color: bg,
          borderRadius: BorderRadius.circular(12),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (role != 'assistant' && role != 'user')
              Text(
                role,
                style: Theme.of(context).textTheme.labelSmall,
              ),
            SelectableText(text),
          ],
        ),
      ),
    );
  }
}
