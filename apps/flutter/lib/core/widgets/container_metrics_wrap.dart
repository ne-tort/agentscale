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
    final metricsUnavailable = containerMetricsUnavailable(container);
    final lastError = containerLastError(container);
    final errorColor = context.appColors.danger;
    final warningColor = context.appColors.warning;

    final stateValue = formatContainerStateValue(container, l10n);
    final lastLaunchValue = formatContainerLastLaunch(container, l10n);
    final uptimeValue = formatContainerUptime(container, l10n);
    final restartsValue = formatContainerRestarts(container, l10n);
    final createdValue = formatContainerCreatedAt(container, l10n);
    final storageValue = _storage(l10n);
    final cpuValue = _cpu(l10n);
    final memoryValue = _memory(l10n);

    final podServiceId = containerPodServiceId(container);
    final k8sPodName = containerK8sPodName(container);

    // Sandbox runtime identity: sandbox/claim ref name + warm/cold launch
    // badge; the full service FQDN goes to the tooltip (too long to inline).
    final isSandbox = containerIsSandboxRuntime(container);
    final containerRuntime = ContainerRuntime.fromJson(container);
    final runtimeRef = isSandbox ? containerRuntimeRefName(container) : null;
    final launchBadge = formatContainerLaunchType(containerRuntime.launchType, l10n);
    final serviceFqdn = containerRuntime.serviceFqdn;

    Widget? runtimeRow;
    if (runtimeRef != null) {
      final content = Wrap(
        spacing: AppSpacing.sm,
        runSpacing: 2,
        crossAxisAlignment: WrapCrossAlignment.center,
        children: [
          Text(runtimeRef),
          if (launchBadge != null)
            _LaunchTypeBadge(
              label: launchBadge,
              warm: containerRuntime.launchType == 'warm',
            ),
        ],
      );
      runtimeRow = AppPreferenceTile(
        title: l10n.containerRuntimeLabel,
        icon: Icons.bolt_outlined,
        subtitle: serviceFqdn != null
            ? Tooltip(message: serviceFqdn, child: content)
            : content,
      );
    }

    final lifecycleRows = <Widget>[
      _readOnlyRow(
        title: l10n.containerStateLabel,
        value: stateValue,
        icon: Icons.circle,
        accentColor: hasError ? errorColor : null,
      ),
      ?runtimeRow,
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
        AppValuePreference<String>(
          title: l10n.containerKubId,
          icon: Icons.dns_outlined,
          value: k8sPodName,
          enabled: false,
          onTap: () {
            Clipboard.setData(ClipboardData(text: k8sPodName));
            AppSnackBar.info(context, l10n.containerKubIdCopied);
          },
          onSave: (_) async {},
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
      if (lastError != null && hasError)
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

    if (containerShowStorageInLifecycle(container) && storageValue != null) {
      lifecycleRows.insert(
        1,
        _readOnlyRow(
          title: l10n.commonStorageBytes,
          value: storageValue,
          icon: Icons.storage_outlined,
        ),
      );
    }

    final resourceTiles = <Widget>[];
    if (containerShowResourceStatTiles(container)) {
      if (cpuValue != null)
        resourceTiles.add(
          StatTile(
            label: l10n.adminContainerMetricsCpu,
            value: cpuValue,
            icon: Icons.speed_outlined,
          ),
        );
      if (memoryValue != null)
        resourceTiles.add(
          StatTile(
            label: l10n.adminContainerMetricsMemory,
            value: memoryValue,
            icon: Icons.memory_outlined,
          ),
        );
      if (storageValue != null)
        resourceTiles.add(
          StatTile(
            label: l10n.commonStorageBytes,
            value: storageValue,
            icon: Icons.storage_outlined,
          ),
        );
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (metricsUnavailable)
          Padding(
            padding: const EdgeInsets.only(bottom: AppSpacing.md),
            child: Material(
              color: warningColor.withValues(alpha: 0.12),
              borderRadius: BorderRadius.circular(AppSpacing.sm),
              child: Padding(
                padding: const EdgeInsets.all(AppSpacing.md),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Icon(Icons.info_outline, color: warningColor, size: 20),
                    const SizedBox(width: AppSpacing.sm),
                    Expanded(
                      child: Text(
                        l10n.adminContainerMetricsUnavailable,
                        style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                              color: warningColor,
                            ),
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
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

/// Warm/cold launch badge next to the sandbox runtime ref (launch_type).
class _LaunchTypeBadge extends StatelessWidget {
  const _LaunchTypeBadge({required this.label, required this.warm});

  final String label;
  final bool warm;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final color = warm ? context.appColors.success : scheme.outline;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(AppSpacing.xs),
        border: Border.all(color: color.withValues(alpha: 0.4)),
      ),
      child: Text(
        label,
        style: Theme.of(context).textTheme.labelSmall?.copyWith(color: color),
      ),
    );
  }
}
