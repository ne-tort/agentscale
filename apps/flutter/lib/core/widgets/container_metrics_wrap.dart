import 'package:flutter/material.dart';

import 'package:prodavan/core/containers/container_runtime_presenter.dart';
import 'package:prodavan/core/format/storage_format.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/stat_tile.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Container page metrics — resource tiles on top, lifecycle tiles below.
class ContainerMetricsWrap extends StatelessWidget {
  const ContainerMetricsWrap({
    super.key,
    required this.container,
    this.runtimeMetrics,
    this.projectMetrics,
  });

  final Map<String, dynamic>? container;
  final Map<String, dynamic>? runtimeMetrics;
  final Map<String, dynamic>? projectMetrics;

  Map<String, dynamic>? get _latest {
    final latest = runtimeMetrics?['latest'];
    if (latest is Map<String, dynamic>) return latest;
    if (latest is Map) return Map<String, dynamic>.from(latest);
    final runtime = runtimeMap(container);
    final nested = runtime?['metrics'];
    if (nested is Map<String, dynamic>) return nested;
    if (nested is Map) return Map<String, dynamic>.from(nested);
    final top = container?['runtime_metrics'];
    if (top is Map<String, dynamic>) return top;
    if (top is Map) return Map<String, dynamic>.from(top);
    return null;
  }

  String _cpu(AppLocalizations l10n) {
    final cpu = _latest?['cpu_millicores'];
    if (cpu is num) return '${cpu.round()}m';
    return l10n.commonEmDash;
  }

  String _memory(AppLocalizations l10n) {
    final mem = _latest?['memory_bytes'];
    if (mem is num) return formatStorageGb(mem);
    return l10n.commonEmDash;
  }

  String _storage(AppLocalizations l10n) {
    final bytes = projectMetrics?['storage_bytes'];
    if (bytes is num) return formatStorageGb(bytes);
    return l10n.commonEmDash;
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final lastError = containerLastError(container);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Wrap(
          spacing: AppSpacing.sm,
          runSpacing: AppSpacing.sm,
          children: [
            StatTile(
              label: l10n.adminContainerMetricsCpu,
              value: _cpu(l10n),
              icon: Icons.speed_outlined,
            ),
            StatTile(
              label: l10n.adminContainerMetricsMemory,
              value: _memory(l10n),
              icon: Icons.memory_outlined,
            ),
            StatTile(
              label: l10n.commonStorageBytes,
              value: _storage(l10n),
              icon: Icons.storage_outlined,
            ),
          ],
        ),
        SizedBox(height: AppSpacing.md),
        Wrap(
          spacing: AppSpacing.sm,
          runSpacing: AppSpacing.sm,
          children: [
            StatTile(
              label: l10n.containerStateLabel,
              value: formatContainerStateValue(container, l10n),
              icon: Icons.circle,
              width: 180,
            ),
            StatTile(
              label: l10n.containerCreatedAt,
              value: formatContainerCreatedAt(container, l10n),
              icon: Icons.add_circle_outline,
              width: 200,
            ),
            StatTile(
              label: l10n.containerStartedAt,
              value: formatContainerStartedAt(container, l10n),
              icon: Icons.play_circle_outline,
              width: 200,
            ),
            StatTile(
              label: l10n.containerUptime,
              value: formatContainerUptime(container, l10n),
              icon: Icons.timer_outlined,
            ),
            StatTile(
              label: l10n.containerRestarts,
              value: formatContainerRestarts(container, l10n),
              icon: Icons.restart_alt_outlined,
            ),
            if (lastError != null)
              StatTile(
                label: l10n.adminContainerLastError,
                value: lastError,
                icon: Icons.error_outline,
                width: 280,
              ),
          ],
        ),
      ],
    );
  }
}
