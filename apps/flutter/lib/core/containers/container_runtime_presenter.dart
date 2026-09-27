import 'package:intl/intl.dart';

import 'package:prodavan/l10n/app_localizations.dart';

/// Typed observed state of an agent sandbox (agent-sandbox conditions mapping).
///
/// Mapping (MIGRATION-DECISIONS.md §1.10):
/// - claim/sandbox Ready=True → running
/// - Ready=False, reason=SandboxSuspended → suspended
/// - Suspended=True, reason=PodTerminating → pausing
/// - PodScheduled=False → provisioning
/// - NotFound → absent
/// - ReconcilerError/InvalidConfiguration → failed
///
/// Legacy states (pre agent-sandbox API) are mapped in [ObservedState.parse]:
/// the legacy string `paused` is normalized to [ObservedState.suspended]
/// (same UX semantics: pod does not respond, data is safe, wakes in seconds).
enum ObservedState {
  preparing,
  provisioning,
  pulling,
  hydrating,
  starting,
  pausing,
  suspended,
  running,
  degraded,
  failed,
  paused,
  absent,
  unknown;

  static ObservedState parse(String? raw) {
    final s = raw?.trim().toLowerCase();
    if (s == null || s.isEmpty) return ObservedState.unknown;
    // Legacy wire value: pre agent-sandbox payloads used `paused`; normalize
    // to suspended (same semantics: data safe on PVC, wake in seconds).
    if (s == 'paused') return ObservedState.suspended;
    for (final v in values) {
      if (v.name == s) return v;
    }
    return ObservedState.unknown;
  }

  /// Legacy wire string (kept for compatibility with old API payloads).
  String get wireName => name;

  bool get isInFlight => switch (this) {
        preparing ||
        provisioning ||
        pulling ||
        hydrating ||
        starting ||
        pausing =>
          true,
        _ => false,
      };

  bool get isTerminal => switch (this) {
        running || failed || suspended || paused || absent => true,
        _ => false,
      };

  bool get isSleeping => this == paused || this == suspended || this == pausing;

  bool get isError => this == failed;
}

/// Typed view over the `runtime` object of a project/container payload.
///
/// Reads `runtime.*` fields with a fallback to the payload root (legacy
/// `observed_state` / `pod_id` / `k8s_pod_name`). All new agent-sandbox
/// contract fields (`sandbox_name`, `claim_name`, `service_fqdn`,
/// `launch_type`, `ready`, `suspended`) are optional and null-safe.
class ContainerRuntime {
  const ContainerRuntime({
    required this.observedState,
    this.sandboxName,
    this.claimName,
    this.serviceFqdn,
    this.launchType,
    this.ready,
    this.suspended,
    this.restarts,
    this.lastError,
    this.stub = false,
    this.podId,
    this.k8sPodName,
    this.startedAt,
    this.metrics,
    this.metricsAvailable,
    this.raw,
  });

  static const _launchTypes = {'warm', 'cold'};

  /// Single static parser: reads `runtime.*` with root-level fallbacks.
  static ContainerRuntime fromJson(Map<String, dynamic>? item) {
    final runtime = runtimeMap(item);

    String? rootString(String key) {
      final v = item?[key];
      if (v == null) return null;
      final s = '$v'.trim();
      return s.isEmpty ? null : s;
    }

    String? rtString(String key) {
      final v = runtime?[key];
      if (v == null) return null;
      final s = '$v'.trim();
      return s.isEmpty ? null : s;
    }

    // observed_state: root first, then runtime (both legacy locations).
    final rawState = rootString('observed_state') ?? rtString('observed_state');
    final observed = ObservedState.parse(rawState);

    String? launch;
    final rawLaunch = rtString('launch_type');
    if (rawLaunch != null && _launchTypes.contains(rawLaunch.toLowerCase())) {
      launch = rawLaunch.toLowerCase();
    }

    final restartsRaw = runtime?['restarts'] ?? item?['restarts'];
    final metricsAvailableRaw = runtime?['metrics_available'];
    // Hoist null-aware reads into locals: x?['k'] is bool ? x?['k'] as bool : ...
    // trips the Dart parser's `is T?` vs ternary disambiguation (CI analyze).
    final readyRaw = runtime?['ready'];
    final suspendedRaw = runtime?['suspended'];

    return ContainerRuntime(
      observedState: observed,
      sandboxName: rtString('sandbox_name'),
      claimName: rtString('claim_name'),
      serviceFqdn: rtString('service_fqdn'),
      launchType: launch,
      ready: readyRaw is bool ? readyRaw : null,
      suspended: suspendedRaw is bool ? suspendedRaw : null,
      restarts: restartsRaw is num ? restartsRaw : null,
      lastError: rtString('last_error') ?? rootString('last_error'),
      stub: runtime?['stub'] == true || item?['stub'] == true,
      podId: rtString('pod_id') ?? rootString('pod_id'),
      k8sPodName: _k8sPodName(runtime, item),
      startedAt: rtString('started_at') ?? rtString('last_started_at') ??
          rootString('started_at') ?? rootString('last_started_at'),
      metrics: runtime?['metrics'] is Map
          ? Map<String, dynamic>.from(runtime?['metrics'] as Map)
          : null,
      metricsAvailable: metricsAvailableRaw is bool ? metricsAvailableRaw : null,
      raw: item,
    );
  }

  static String? _k8sPodName(Map<String, dynamic>? runtime, Map<String, dynamic>? item) {
    // Legacy `stub` payloads hide the pod name; `object-ws:` refs are legacy too.
    if (runtime?['stub'] == true) return null;
    final name = runtime?['k8s_pod_name'] ?? runtime?['runtime_ref'] ?? item?['k8s_pod_name'];
    if (name == null) return null;
    final s = '$name'.trim();
    if (s.isEmpty || s.startsWith('object-ws:')) return null;
    return s;
  }

  final ObservedState observedState;
  final String? sandboxName;
  final String? claimName;
  final String? serviceFqdn;

  /// 'warm' | 'cold' | null (legacy payloads have no launch_type).
  final String? launchType;
  final bool? ready;
  final bool? suspended;
  final num? restarts;
  final String? lastError;
  final bool stub;
  final String? podId;
  final String? k8sPodName;
  final String? startedAt;
  final Map<String, dynamic>? metrics;
  final bool? metricsAvailable;

  /// Original payload (for legacy string-based helpers).
  final Map<String, dynamic>? raw;

  bool get isRunning => observedState == ObservedState.running;
  bool get isHealthy => !stub && isRunning;
}

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

/// Public observed_state accessor for job labels / buttons.
String? containerObservedState(Map<String, dynamic>? item) => _observedState(item);

/// Normalized observed state: legacy `paused` is reported as `suspended`
/// (agent-sandbox semantics). Keep old string consumers via
/// [containerObservedState]; new consumers use this.
ObservedState containerObservedEnum(Map<String, dynamic>? item) =>
    ContainerRuntime.fromJson(item).observedState;

const _inFlightObservedStates = {
  'preparing',
  'provisioning',
  'pulling',
  'hydrating',
  'starting',
  'pausing',
};

bool containerIsInFlight(Map<String, dynamic>? item) {
  final state = _observedState(item);
  return state != null && _inFlightObservedStates.contains(state);
}

bool projectHasLivePod(Map<String, dynamic>? project) {
  if (runtimeMap(project) != null) return true;
  final state = _observedState(project);
  if (state == null) return false;
  return state != 'absent' && state != 'unknown';
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
    'pulling' => l10n.containerObservedPulling,
    'hydrating' => l10n.containerObservedHydrating,
    'starting' => l10n.containerObservedStarting,
    'suspended' => l10n.containerObservedSuspended,
    'pausing' => l10n.containerObservedPausing,
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

String? containerLastLaunchIso(Map<String, dynamic>? item) {
  return _runtimeString(item, 'started_at') ?? _runtimeString(item, 'last_started_at');
}

String formatContainerLastLaunch(Map<String, dynamic>? item, AppLocalizations l10n) {
  return formatContainerTimestamp(containerLastLaunchIso(item), l10n);
}

bool containerHasError(Map<String, dynamic>? item) {
  final state = _observedState(item);
  if (state == 'failed') return true;
  final err = containerLastError(item);
  if (err == null) return false;
  return !_isMetricsOnlyError(err);
}

bool containerMetricsUnavailable(Map<String, dynamic>? item) {
  if (_observedState(item) != 'running') return false;
  final runtime = runtimeMap(item);
  if (runtime == null) return true;
  final available = runtime['metrics_available'];
  if (available is bool) return !available;
  final metrics = runtime['metrics'];
  if (metrics is Map && metrics['cpu_millicores'] != null) return false;
  return true;
}

/// Sleeping pod (paused / suspended / pausing) — agent data is safe on PVC,
/// wake takes seconds (agent-sandbox suspend/resume).
bool containerIsPaused(Map<String, dynamic>? item) {
  final rt = ContainerRuntime.fromJson(item);
  if (rt.observedState.isSleeping) return true;
  return item?['status'] == 'paused';
}

bool containerIsSuspended(Map<String, dynamic>? item) {
  final rt = ContainerRuntime.fromJson(item);
  return rt.observedState == ObservedState.suspended ||
      rt.observedState == ObservedState.pausing;
}

bool containerShowResourceStatTiles(Map<String, dynamic>? item) =>
    !containerHasError(item) &&
    !containerMetricsUnavailable(item) &&
    !containerIsPaused(item);

bool containerShowStorageInLifecycle(Map<String, dynamic>? item) =>
    containerHasError(item) ||
    containerMetricsUnavailable(item) ||
    containerIsPaused(item);

bool _isMetricsOnlyError(String err) {
  final lower = err.toLowerCase();
  return lower.contains('metrics-server')
      || lower.contains('metrics not')
      || lower.contains('metrics unavailable');
}

bool containerMetricHasValue(String formatted, AppLocalizations l10n) {
  return formatted.trim().isNotEmpty && formatted != l10n.commonEmDash;
}

String formatContainerCreatedAt(Map<String, dynamic>? item, AppLocalizations l10n) {
  final created = _runtimeString(item, 'pod_created_at') ??
      _runtimeString(item, 'k8s_created_at');
  return formatContainerTimestamp(created, l10n);
}

@Deprecated('Use formatContainerLastLaunch')
String formatContainerStartedAt(Map<String, dynamic>? item, AppLocalizations l10n) {
  return formatContainerLastLaunch(item, l10n);
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
  final rt = ContainerRuntime.fromJson(item);
  // Sleeping sandbox: data is safe on PVC, resume is the dedicated action.
  if (rt.observedState.isSleeping) return false;
  // Provisioning / pulling / … is progress, not an attention state.
  if (rt.observedState.isInFlight) return false;
  final observed = _observedState(item);
  if (observed != null) {
    return observed != 'running' && observed != 'degraded';
  }
  return runtimeMap(item) == null;
}

/// Project list row — error if status=error or active with unhealthy container.
bool projectShowsContainerError(Map<String, dynamic>? project) {
  if (project?['status'] == 'error') return true;
  if (project?['status'] != 'active') return false;
  final observed = project?['observed_state'] as String?;
  if (observed == null || observed.isEmpty) return false;
  const ok = {
    'running',
    'paused',
    'suspended',
    'pausing',
    'preparing',
    'provisioning',
    'hydrating',
    'starting',
    'pulling',
    'degraded',
  };
  return !ok.contains(observed);
}

/// Active, errored, or paused — transcript readable from DB even if pod is down.
bool projectChatReadable(Map<String, dynamic>? project) {
  if (project == null) return false;
  final status = project['status'];
  return status == 'active' || status == 'error' || status == 'paused';
}

/// Pod running — required to send messages / stream SSE.
bool projectChatSendable(Map<String, dynamic>? project) {
  if (project == null || project['status'] != 'active') return false;
  return containerRuntimeHealthy(project);
}

/// Readable but not sendable — composer wake (resume / reload) via tap.
bool projectChatNeedsWake(Map<String, dynamic>? project) {
  return projectChatReadable(project) && !projectChatSendable(project);
}

/// Sleeping agent (paused / suspended / pausing): wake is a fast resume
/// (seconds with agent-sandbox); a separate sub-case of needsWake.
bool projectChatSuspended(Map<String, dynamic>? project) {
  if (project == null) return false;
  if (project['status'] == 'paused') return true;
  final rt = ContainerRuntime.fromJson(project);
  return rt.observedState.isSleeping;
}

@Deprecated('Use projectChatSendable')
bool projectChatAvailable(Map<String, dynamic>? project) => projectChatSendable(project);

/// Live pod the agent chat can talk to. `degraded` is a metrics-only
/// condition — the agent itself still answers, so the composer stays
/// sendable (degraded only feeds the metrics banner).
bool containerRuntimeHealthy(Map<String, dynamic>? item) {
  if (item?['runtime']?['stub'] == true) return false;
  final state = _observedState(item);
  return state == 'running' || state == 'degraded';
}

String? containerPodServiceId(Map<String, dynamic>? item) {
  final runtime = runtimeMap(item);
  final id = runtime?['pod_id'] ?? item?['pod_id'];
  if (id == null) return null;
  final s = '$id'.trim();
  return s.isEmpty ? null : s;
}

String? containerK8sPodName(Map<String, dynamic>? item) {
  final runtime = runtimeMap(item);
  if (runtime?['stub'] == true) return null;
  final name = runtime?['k8s_pod_name'] ?? runtime?['runtime_ref'];
  if (name == null) return null;
  final s = '$name'.trim();
  if (s.isEmpty || s.startsWith('object-ws:')) return null;
  return s;
}

/// agent-sandbox runtime view (sandbox_name/claim_name present) — there is
/// no k8s pod behind the container, metrics are not collected by design.
bool containerIsSandboxRuntime(Map<String, dynamic>? item) {
  final rt = ContainerRuntime.fromJson(item);
  return rt.sandboxName != null || rt.claimName != null;
}

/// Runtime identifier for the Runtime row: agent-sandbox name (claim
/// fallback) in sandbox mode, legacy k8s pod name otherwise.
String? containerRuntimeRefName(Map<String, dynamic>? item) {
  final rt = ContainerRuntime.fromJson(item);
  return rt.sandboxName ?? rt.claimName ?? containerK8sPodName(item);
}
