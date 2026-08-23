import 'package:flutter/material.dart';

/// Compact project lifecycle status chip (L05/L07).
class ProjectStatusChip extends StatelessWidget {
  const ProjectStatusChip({
    super.key,
    required this.status,
    this.dense = true,
  });

  final String status;
  final bool dense;

  static bool isPaused(String? status) => status == 'paused';

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final paused = isPaused(status);
    final label = status.isEmpty ? 'unknown' : status;
    return Chip(
      visualDensity: dense ? VisualDensity.compact : VisualDensity.standard,
      avatar: Icon(
        paused ? Icons.pause_circle_filled : Icons.play_circle_outline,
        size: 16,
        color: paused ? scheme.onSecondaryContainer : scheme.onPrimaryContainer,
      ),
      label: Text(label),
      backgroundColor: paused ? scheme.secondaryContainer : scheme.primaryContainer,
      side: BorderSide.none,
    );
  }
}
