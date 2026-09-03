import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_spacing.dart';

/// Dialog-style loading placeholder for chat transcript bootstrap.
class ChatTranscriptSkeleton extends StatefulWidget {
  const ChatTranscriptSkeleton({super.key});

  @override
  State<ChatTranscriptSkeleton> createState() => _ChatTranscriptSkeletonState();
}

class _ChatTranscriptSkeletonState extends State<ChatTranscriptSkeleton> with SingleTickerProviderStateMixin {
  late final AnimationController _pulse;

  @override
  void initState() {
    super.initState();
    _pulse = AnimationController(vsync: this, duration: const Duration(milliseconds: 900))..repeat(reverse: true);
  }

  @override
  void dispose() {
    _pulse.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final bubbleColor = scheme.onSurface.withValues(alpha: 0.08);
    final widths = <double>[0.62, 0.48, 0.72, 0.55, 0.68, 0.42];
    final alignments = <Alignment>[
      Alignment.centerLeft,
      Alignment.centerRight,
      Alignment.centerLeft,
      Alignment.centerRight,
      Alignment.centerLeft,
      Alignment.centerRight,
    ];

    return FadeTransition(
      opacity: Tween<double>(begin: 0.45, end: 0.95).animate(CurvedAnimation(parent: _pulse, curve: Curves.easeInOut)),
      child: ListView(
        padding: EdgeInsets.all(AppSpacing.md),
        physics: const NeverScrollableScrollPhysics(),
        children: [
          for (var i = 0; i < widths.length; i++)
            Align(
              alignment: alignments[i],
              child: Container(
                margin: EdgeInsets.only(bottom: AppSpacing.sm),
                width: MediaQuery.sizeOf(context).width * widths[i],
                height: i.isEven ? 44 : 36,
                decoration: BoxDecoration(
                  color: bubbleColor,
                  borderRadius: BorderRadius.circular(12),
                ),
              ),
            ),
        ],
      ),
    );
  }
}
