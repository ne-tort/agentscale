import 'package:flutter/material.dart';

/// Empty-chat placeholder: a quiet, dialog-shaped bubble with three muted
/// dots — the same visual language as the streaming indicator, with no text.
///
/// [highlighted] is driven by the OS drag & drop hover: the bubble gets a
/// primary-tinted border, brighter dots and a subtle scale-up so the chat
/// visibly "opens up" while a file is being dragged in.
class ChatEmptyPlaceholder extends StatelessWidget {
  const ChatEmptyPlaceholder({super.key, this.highlighted = false});

  final bool highlighted;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final borderColor = highlighted
        ? scheme.primary.withValues(alpha: 0.55)
        : scheme.onSurfaceVariant.withValues(alpha: 0.16);
    final dotColor = highlighted
        ? scheme.primary.withValues(alpha: 0.65)
        : scheme.onSurfaceVariant.withValues(alpha: 0.28);
    return Center(
      child: AnimatedScale(
        scale: highlighted ? 1.03 : 1.0,
        duration: const Duration(milliseconds: 150),
        curve: Curves.easeOutCubic,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 150),
          curve: Curves.easeOutCubic,
          padding: const EdgeInsets.symmetric(horizontal: 28, vertical: 22),
          decoration: BoxDecoration(
            color: scheme.surfaceContainerLow,
            borderRadius: BorderRadius.circular(18),
            border: Border.all(
              color: borderColor,
              width: highlighted ? 1.4 : 1.0,
            ),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              for (var i = 0; i < 3; i++)
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 3),
                  child: Container(
                    width: 6,
                    height: 6,
                    decoration: BoxDecoration(shape: BoxShape.circle, color: dotColor),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

/// Full-area veil shown while files are dragged over the chat transcript.
///
/// Deliberately label-free: a soft primary tint with a rounded outline tells
/// the user "drop here" without a single word. The composer field is never
/// covered (the veil only spans the transcript region).
class ChatDropVeil extends StatelessWidget {
  const ChatDropVeil({super.key});

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.all(8),
      child: DecoratedBox(
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(20),
          border: Border.all(
            color: scheme.primary.withValues(alpha: 0.45),
            width: 1.5,
          ),
          color: scheme.primary.withValues(alpha: 0.05),
        ),
        child: const SizedBox.expand(),
      ),
    );
  }
}
