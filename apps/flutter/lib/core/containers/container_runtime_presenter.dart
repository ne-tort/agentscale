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

String formatContainerRuntimeCell(Map<String, dynamic>? item, AppLocalizations l10n) {
  final observed = _observedState(item);
  if (observed != null) return _formatObservedState(observed, l10n);

  final runtime = runtimeMap(item);
  if (runtime == null) {
    return switch (item?['status'] as String?) {
      'paused' => l10n.adminContainerRuntimePausedShort,
      'active' => l10n.adminContainerRuntimeNotStartedShort,
      _ => l10n.commonEmDash,
    };
  }
  final phase = item?['k8s_phase'] as String? ?? runtime['phase'] as String?;
  if (phase != null && phase.trim().isNotEmpty) return phase.trim();
  return l10n.commonEmDash;
}

String formatContainerRuntimeDetail(Map<String, dynamic>? item, AppLocalizations l10n) {
  final runtime = runtimeMap(item);
  if (runtime == null && _observedState(item) == null) {
    final lastError = item?['last_error'];
    if (lastError != null && '$lastError'.trim().isNotEmpty) {
      return '${l10n.adminContainerLastError}: ${'$lastError'.trim()}';
    }
    return switch (item?['status'] as String?) {
      'paused' => l10n.adminContainerRuntimePaused,
      'active' => l10n.adminContainerRuntimeNotStarted,
      'error' => l10n.commonEmDash,
      _ => l10n.commonEmDash,
    };
  }

  final lines = <String>[];
  final observed = _observedState(item);
  if (observed != null) {
    lines.add('${l10n.containerObservedStateLabel}: ${_formatObservedState(observed, l10n)}');
  }

  final phase = item?['k8s_phase'] as String? ?? runtime?['phase'] as String?;
  if (phase != null && phase.trim().isNotEmpty) {
    lines.add('${l10n.adminContainerK8sPhase}: ${phase.trim()}');
  }

  final orchestrator = runtime?['orchestrator_status'] as String? ?? runtime?['status'] as String?;
  if (orchestrator != null && orchestrator.trim().isNotEmpty) {
    lines.add('${l10n.adminContainerOrchestratorStatus}: ${orchestrator.trim()}');
  }

  final desired = runtime?['desired_state'] as String?;
  if (desired != null && desired.trim().isNotEmpty) {
    lines.add('${l10n.adminContainerDesiredState}: ${desired.trim()}');
  }

  final ready = runtime?['ready'];
  if (ready is bool) {
    lines.add(
      ready ? l10n.adminContainerPodReadyTrue : l10n.adminContainerPodReadyFalse,
    );
  }

  final restarts = runtime?['restarts'];
  if (restarts is num) {
    lines.add('${l10n.adminContainerPodRestarts}: $restarts');
  }

  final metrics = runtime?['metrics'] ?? item?['runtime_metrics'];
  final metricsDegraded = runtime?['metrics_degraded'] == true || item?['metrics_degraded'] == true;
  if (metrics is Map && observed == 'running') {
    final cpu = metrics['cpu_millicores'];
    final mem = metrics['memory_bytes'];
    if (cpu is num) {
      lines.add('${l10n.adminContainerMetricsCpu}: ${cpu.round()}m');
    }
    if (mem is num) {
      lines.add('${l10n.adminContainerMetricsMemory}: ${_formatBytes(mem, l10n)}');
    }
  } else if (observed == 'starting') {
    lines.add(l10n.containerMetricsAwaiting);
  } else if (metricsDegraded || observed == 'degraded') {
    lines.add(l10n.adminContainerMetricsUnavailable);
  }

  final lastError = runtime?['last_error'] ?? item?['last_error'];
  if (lastError != null && '$lastError'.trim().isNotEmpty) {
    lines.add('${l10n.adminContainerLastError}: ${'$lastError'.trim()}');
  }

  return lines.isEmpty ? l10n.commonEmDash : lines.join('\n');
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

String _formatBytes(num bytes, AppLocalizations l10n) {
  final value = bytes.toDouble();
  if (value >= 1024 * 1024 * 1024) {
    return '${(value / (1024 * 1024 * 1024)).toStringAsFixed(1)} GiB';
  }
  if (value >= 1024 * 1024) {
    return '${(value / (1024 * 1024)).toStringAsFixed(1)} MiB';
  }
  if (value >= 1024) {
    return '${(value / 1024).toStringAsFixed(1)} KiB';
  }
  return '${value.round()} B';
}
