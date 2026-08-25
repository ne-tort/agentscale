import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/l10n/app_localizations.dart';

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
    final l10n = AppLocalizations.of(context);
final colors = context.appColors;
    final paused = isPaused(status);
    final label = status.isEmpty ? l10n.commonUnknown : status;
    return Chip(
      visualDensity: dense ? VisualDensity.compact : VisualDensity.standard,
      avatar: Icon(
        paused ? Icons.pause_circle_filled : Icons.play_circle_outline,
        size: 16,
        color: paused ? colors.muted : colors.onPrimaryContainer,
      ),
      label: Text(label),
      backgroundColor: paused ? colors.surfaceContainer : colors.primaryContainer,
      side: BorderSide.none,
    );
  }
}
