import 'package:prodavan/l10n/app_localizations.dart';

Map<String, dynamic>? runtimeMap(Map<String, dynamic>? item) {
  final runtime = item?['runtime'];
  if (runtime is Map<String, dynamic>) return runtime;
  if (runtime is Map) return Map<String, dynamic>.from(runtime);
  return null;
}

String formatContainerRuntimeCell(Map<String, dynamic>? item, AppLocalizations l10n) {
  final runtime = runtimeMap(item);
  if (runtime == null) {
    return switch (item?['status'] as String?) {
      'paused' => l10n.adminContainerRuntimePausedShort,
      'active' => l10n.adminContainerRuntimeNotStartedShort,
      _ => l10n.commonEmDash,
    };
  }
  final phase = item?['k8s_phase'] as String? ??
      runtime['phase'] as String? ??
      runtime['status'] as String?;
  if (phase != null && phase.trim().isNotEmpty) return phase.trim();
  final podStatus = runtime['status'] as String?;
  if (podStatus != null && podStatus.trim().isNotEmpty) return podStatus.trim();
  return l10n.commonEmDash;
}

String formatContainerRuntimeDetail(Map<String, dynamic>? item, AppLocalizations l10n) {
  final runtime = runtimeMap(item);
  if (runtime == null) {
    return switch (item?['status'] as String?) {
      'paused' => l10n.adminContainerRuntimePaused,
      'active' => l10n.adminContainerRuntimeNotStarted,
      _ => l10n.commonEmDash,
    };
  }

  final lines = <String>[];
  final phase = item?['k8s_phase'] as String? ??
      runtime['phase'] as String? ??
      runtime['status'] as String?;
  if (phase != null && phase.trim().isNotEmpty) {
    lines.add('${l10n.adminContainerK8sPhase}: ${phase.trim()}');
  }

  final podStatus = runtime['status'] as String?;
  if (podStatus != null && podStatus.trim().isNotEmpty) {
    lines.add('${l10n.adminContainerPodStatus}: ${podStatus.trim()}');
  }

  final desired = runtime['desired_state'] as String?;
  if (desired != null && desired.trim().isNotEmpty) {
    lines.add('${l10n.adminContainerDesiredState}: ${desired.trim()}');
  }

  final ready = runtime['ready'];
  if (ready is bool) {
    lines.add(
      ready ? l10n.adminContainerPodReadyTrue : l10n.adminContainerPodReadyFalse,
    );
  }

  final restarts = runtime['restarts'];
  if (restarts is num) {
    lines.add('${l10n.adminContainerPodRestarts}: $restarts');
  }

  final metrics = runtime['metrics'] ?? item?['runtime_metrics'];
  final metricsDegraded = runtime['metrics_degraded'] == true || item?['metrics_degraded'] == true;
  if (metrics is Map) {
    final cpu = metrics['cpu_millicores'];
    final mem = metrics['memory_bytes'];
    if (cpu is num) {
      lines.add('${l10n.adminContainerMetricsCpu}: ${cpu.round()}m');
    }
    if (mem is num) {
      lines.add('${l10n.adminContainerMetricsMemory}: ${_formatBytes(mem, l10n)}');
    }
  } else if (metricsDegraded || phase == 'Running' || podStatus == 'running') {
    lines.add(l10n.adminContainerMetricsUnavailable);
  }

  final lastError = runtime['last_error'] ?? item?['last_error'];
  if (lastError != null && '$lastError'.trim().isNotEmpty) {
    lines.add('${l10n.adminContainerLastError}: ${'$lastError'.trim()}');
  }

  return lines.isEmpty ? l10n.commonEmDash : lines.join('\n');
}

bool containerRuntimeNeedsAttention(Map<String, dynamic>? item) {
  if (item?['status'] != 'active') return false;
  return runtimeMap(item) == null;
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
