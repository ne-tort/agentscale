import 'package:intl/intl.dart';

import 'package:prodavan/l10n/app_localizations.dart';

Map<String, dynamic>? runtimeMap(Map<String, dynamic>? item) {
  final runtime = item?['runtime'];
  if (runtime is Map<String, dynamic>) return runtime;
  if (runtime is Map) return Map<String, dynamic>.from(runtime);
  return null;
}

String? _observedState(Map<String, dynamic>? item) {
  final top = item?['observed_state'] as String?;
  if (top != null && top.trim().isNotEmpty) return top.trim();
  final runtime = runtimeMap(item);
  final nested = runtime?['observed_state'] as String?;
  if (nested != null && nested.trim().isNotEmpty) return nested.trim();
  return null;
}

String? _runtimeString(Map<String, dynamic>? item, String key) {
  final runtime = runtimeMap(item);
  final v = runtime?[key] ?? item?[key];
  if (v == null) return null;
  final s = '$v'.trim();
  return s.isEmpty ? null : s;
}

String _formatObservedState(String state, AppLocalizations l10n) {
  return switch (state) {
    'preparing' => l10n.containerObservedPreparing,
    'provisioning' => l10n.containerObservedProvisioning,
    'hydrating' => l10n.containerObservedHydrating,
    'starting' => l10n.containerObservedStarting,
    'running' => l10n.containerObservedRunning,
    'degraded' => l10n.containerObservedDegraded,
    'failed' => l10n.containerObservedFailed,
    'paused' => l10n.containerObservedPaused,
    'absent' => l10n.containerObservedAbsent,
    _ => l10n.containerObservedUnknown,
  };
}

/// Localized unified container state for StatTile value.
String formatContainerStateValue(Map<String, dynamic>? item, AppLocalizations l10n) {
  final observed = _observedState(item);
  if (observed != null) return _formatObservedState(observed, l10n);

  final runtime = runtimeMap(item);
  if (runtime == null) {
    return switch (item?['status'] as String?) {
      'paused' => l10n.containerObservedPaused,
      'active' => l10n.adminContainerRuntimeNotStartedShort,
      _ => l10n.commonEmDash,
    };
  }
  return l10n.commonEmDash;
}

String formatContainerRuntimeCell(Map<String, dynamic>? item, AppLocalizations l10n) {
  return formatContainerStateValue(item, l10n);
}

String formatContainerTimestamp(String? iso, AppLocalizations l10n) {
  if (iso == null || iso.trim().isEmpty) return l10n.commonEmDash;
  try {
    final dt = DateTime.parse(iso).toLocal();
    return DateFormat('dd.MM.yyyy HH:mm').format(dt);
  } catch (_) {
    return l10n.commonEmDash;
  }
}

String formatContainerCreatedAt(Map<String, dynamic>? item, AppLocalizations l10n) {
  final created = _runtimeString(item, 'pod_created_at') ??
      _runtimeString(item, 'k8s_created_at');
  return formatContainerTimestamp(created, l10n);
}

String formatContainerStartedAt(Map<String, dynamic>? item, AppLocalizations l10n) {
  return formatContainerTimestamp(_runtimeString(item, 'started_at'), l10n);
}

String formatContainerUptime(Map<String, dynamic>? item, AppLocalizations l10n) {
  final started = _runtimeString(item, 'started_at');
  if (started == null) return l10n.commonEmDash;
  try {
    final start = DateTime.parse(started).toUtc();
    final diff = DateTime.now().toUtc().difference(start);
    if (diff.isNegative) return l10n.commonEmDash;
    return _formatDuration(diff, l10n);
  } catch (_) {
    return l10n.commonEmDash;
  }
}

String formatContainerRestarts(Map<String, dynamic>? item, AppLocalizations l10n) {
  final runtime = runtimeMap(item);
  final restarts = runtime?['restarts'] ?? item?['restarts'];
  if (restarts is num) return '${restarts.round()}';
  return l10n.commonEmDash;
}

String? containerLastError(Map<String, dynamic>? item) {
  final runtime = runtimeMap(item);
  final err = runtime?['last_error'] ?? item?['last_error'];
  if (err == null) return null;
  final s = '$err'.trim();
  return s.isEmpty ? null : s;
}

String _formatDuration(Duration diff, AppLocalizations l10n) {
  final days = diff.inDays;
  final hours = diff.inHours.remainder(24);
  final minutes = diff.inMinutes.remainder(60);
  final parts = <String>[];
  if (days > 0) parts.add(l10n.containerDurationDays(days));
  if (hours > 0) parts.add(l10n.containerDurationHours(hours));
  if (minutes > 0 || parts.isEmpty) parts.add(l10n.containerDurationMinutes(minutes));
  return parts.join(' ');
}

@Deprecated('Use ContainerMetricsWrap StatTiles instead')
String formatContainerRuntimeDetail(Map<String, dynamic>? item, AppLocalizations l10n) {
  final lines = <String>[formatContainerStateValue(item, l10n)];
  final err = containerLastError(item);
  if (err != null) lines.add('${l10n.adminContainerLastError}: $err');
  return lines.join('\n');
}

bool containerRuntimeNeedsAttention(Map<String, dynamic>? item) {
  if (item?['status'] != 'active') return false;
  final observed = _observedState(item);
  if (observed != null) {
    return observed != 'running';
  }
  return runtimeMap(item) == null;
}

bool containerRuntimeHealthy(Map<String, dynamic>? item) {
  return _observedState(item) == 'running';
}
