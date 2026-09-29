import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_spacing.dart';

/// Empty-chat placeholder: quiet dialog-shaped skeleton plates pinned to the
/// TOP of the chat (not centered) — the same visual language as
/// [ChatTranscriptSkeleton]: grey placeholder "picture" plates (avatar dot,
/// message card, text lines, a small reply bubble), gently pulsing.
///
/// [highlighted] is driven by the OS drag & drop hover: plates take a
/// primary tint so the chat visibly opens up for the incoming file — no
/// labels anywhere.
class ChatEmptyPlaceholder extends StatefulWidget {
  const ChatEmptyPlaceholder({super.key, this.highlighted = false});

  final bool highlighted;

  @override
  State<ChatEmptyPlaceholder> createState() => _ChatEmptyPlaceholderState();
}

class _ChatEmptyPlaceholderState extends State<ChatEmptyPlaceholder>
    with SingleTickerProviderStateMixin {
  late final AnimationController _pulse = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 900),
  )..repeat(reverse: true);

  @override
  void dispose() {
    _pulse.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final plateColor = widget.highlighted
        ? scheme.primary.withValues(alpha: 0.14)
        : scheme.onSurface.withValues(alpha: 0.08);

    return FadeTransition(
      opacity: Tween<double>(begin: 0.5, end: 0.9)
          .animate(CurvedAnimation(parent: _pulse, curve: Curves.easeInOut)),
      child: ListView(
        padding: EdgeInsets.all(AppSpacing.md),
        physics: const NeverScrollableScrollPhysics(),
        children: [
          // Assistant side: avatar placeholder + message card + text lines.
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Container(
                width: 34,
                height: 34,
                decoration: BoxDecoration(shape: BoxShape.circle, color: plateColor),
              ),
              const SizedBox(width: AppSpacing.sm),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Container(
                      height: 56,
                      decoration: BoxDecoration(
                        color: plateColor,
                        borderRadius: BorderRadius.circular(14),
                      ),
                    ),
                    const SizedBox(height: AppSpacing.xs),
                    _line(plateColor, 0.78),
                    const SizedBox(height: AppSpacing.xs),
                    _line(plateColor, 0.55),
                    const SizedBox(height: AppSpacing.xs),
                    _line(plateColor, 0.66),
                  ],
                ),
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.sm),
          // User side: small reply bubble placeholder.
          Align(
            alignment: Alignment.centerRight,
            child: FractionallySizedBox(
              alignment: Alignment.centerRight,
              widthFactor: 0.34,
              child: Container(
                height: 30,
                decoration: BoxDecoration(
                  color: plateColor,
                  borderRadius: BorderRadius.circular(12),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _line(Color color, double factor) => FractionallySizedBox(
        alignment: Alignment.centerLeft,
        widthFactor: factor,
        child: Container(
          height: 10,
          decoration: BoxDecoration(
            color: color,
            borderRadius: BorderRadius.circular(5),
          ),
        ),
      );
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
