import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'package:prodavan/core/containers/container_runtime_presenter.dart';
import 'package:prodavan/core/format/storage_format.dart';
import 'package:prodavan/core/preferences/app_preference_tile.dart';
import 'package:prodavan/core/preferences/app_value_preference.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_snack_bar.dart';
import 'package:prodavan/core/widgets/stat_tile.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Container page metrics — resource StatTiles on top, lifecycle preference rows below.
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

  String? _cpu(AppLocalizations l10n) {
    final cpu = _latest?['cpu_millicores'];
    if (cpu is num) return '${cpu.round()}m';
    return null;
  }

  String? _memory(AppLocalizations l10n) {
    final mem = _latest?['memory_bytes'];
    if (mem is num) return formatStorageGb(mem);
    return null;
  }

  String? _storage(AppLocalizations l10n) {
    final bytes = projectMetrics?['storage_bytes'];
    if (bytes is num) return formatStorageGb(bytes);
    return null;
  }

  Widget _readOnlyRow({
    required String title,
    required String value,
    required IconData icon,
    Color? accentColor,
  }) {
    return AppPreferenceTile(
      title: title,
      icon: icon,
      accentColor: accentColor,
      subtitle: Text(value),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final hasError = containerHasError(container);
    final lastError = containerLastError(container);
    final errorColor = context.appColors.danger;

    final stateValue = formatContainerStateValue(container, l10n);
    final lastLaunchValue = formatContainerLastLaunch(container, l10n);
    final uptimeValue = formatContainerUptime(container, l10n);
    final restartsValue = formatContainerRestarts(container, l10n);
    final createdValue = formatContainerCreatedAt(container, l10n);
    final storageValue = _storage(l10n);

    final podServiceId = containerPodServiceId(container);
    final k8sPodName = containerK8sPodName(container);

    final lifecycleRows = <Widget>[
      _readOnlyRow(
        title: l10n.containerStateLabel,
        value: stateValue,
        icon: Icons.circle,
        accentColor: hasError ? errorColor : null,
      ),
      if (podServiceId != null)
        AppValuePreference<String>(
          title: l10n.containerPodServiceId,
          icon: Icons.tag_outlined,
          value: podServiceId,
          enabled: false,
          onTap: () {
            Clipboard.setData(ClipboardData(text: podServiceId));
            AppSnackBar.info(context, l10n.containerPodIdCopied);
          },
          onSave: (_) async {},
        ),
      if (k8sPodName != null)
        _readOnlyRow(
          title: l10n.containerK8sPodName,
          value: k8sPodName,
          icon: Icons.dns_outlined,
        ),
      if (containerMetricHasValue(lastLaunchValue, l10n))
        _readOnlyRow(
          title: l10n.containerLastLaunch,
          value: lastLaunchValue,
          icon: Icons.play_circle_outline,
        ),
      if (!hasError && containerMetricHasValue(uptimeValue, l10n))
        _readOnlyRow(
          title: l10n.containerUptime,
          value: uptimeValue,
          icon: Icons.timer_outlined,
        ),
      if (!hasError && containerMetricHasValue(restartsValue, l10n))
        _readOnlyRow(
          title: l10n.containerRestarts,
          value: restartsValue,
          icon: Icons.restart_alt_outlined,
        ),
      if (lastError != null)
        AppValuePreference<String>(
          title: l10n.adminContainerLastError,
          icon: Icons.error_outline,
          value: lastError,
          enabled: false,
          onTap: () {
            Clipboard.setData(ClipboardData(text: lastError));
            AppSnackBar.info(context, l10n.containerErrorCopied);
          },
          onSave: (_) async {},
        ),
      if (containerMetricHasValue(createdValue, l10n))
        _readOnlyRow(
          title: l10n.containerCreatedAt,
          value: createdValue,
          icon: Icons.add_circle_outline,
        ),
    ];

    if (hasError && storageValue != null) {
      lifecycleRows.insert(
        1,
        _readOnlyRow(
          title: l10n.commonStorageBytes,
          value: storageValue,
          icon: Icons.storage_outlined,
        ),
      );
    }

    final resourceTiles = <Widget>[
      if (!hasError && _cpu(l10n) != null)
        StatTile(
          label: l10n.adminContainerMetricsCpu,
          value: _cpu(l10n)!,
          icon: Icons.speed_outlined,
        ),
      if (!hasError && _memory(l10n) != null)
        StatTile(
          label: l10n.adminContainerMetricsMemory,
          value: _memory(l10n)!,
          icon: Icons.memory_outlined,
        ),
      if (!hasError && storageValue != null)
        StatTile(
          label: l10n.commonStorageBytes,
          value: storageValue,
          icon: Icons.storage_outlined,
        ),
    ];

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (resourceTiles.isNotEmpty)
          Wrap(
            spacing: AppSpacing.sm,
            runSpacing: AppSpacing.sm,
            children: resourceTiles,
          ),
        if (resourceTiles.isNotEmpty) SizedBox(height: AppSpacing.md),
        ...lifecycleRows,
      ],
    );
  }
}
