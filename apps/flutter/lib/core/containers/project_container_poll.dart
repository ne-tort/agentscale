import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/containers/container_runtime_presenter.dart';

/// Terminal observed states: the poll stops once the sandbox settles into
/// one of these (agent-sandbox conditions reach a stable value).
const _terminalObservedStates = {
  'running',
  'failed',
  'suspended',
  'paused',
  'absent',
};

bool containerObservedSettled(Map<String, dynamic>? item) {
  final runtime = item?['runtime'];
  final merged = <String, dynamic>{
    if (item != null) ...item,
    if (runtime is Map) 'runtime': runtime,
  };
  final state = merged['observed_state'] as String? ??
      (runtime is Map ? runtime['observed_state'] as String? : null);
  if (state == null || state.trim().isEmpty) return false;
  return _terminalObservedStates.contains(state.trim());
}

/// agent-sandbox suspend/resume is seconds-scale — 5 min is plenty
/// (warm claim ~2s, cold start with pull is the long tail).
const containerPollDefaultTimeout = Duration(minutes: 5);

/// Poll project container until observed_state settles or [timeout] elapses.
Future<Map<String, dynamic>?> pollProjectContainerUntilSettled({
  required ProdavanApi api,
  required String projectId,
  Duration interval = const Duration(seconds: 1),
  Duration timeout = containerPollDefaultTimeout,
  void Function(Map<String, dynamic>? item)? onTick,
}) async {
  final deadline = DateTime.now().add(timeout);
  Map<String, dynamic>? last;
  while (DateTime.now().isBefore(deadline)) {
    last = await api.getProjectContainer(projectId);
    onTick?.call(last);
    if (containerObservedSettled(last)) return last;
    await Future<void>.delayed(interval);
  }
  return last;
}

String? containerObservedFailureMessage(Map<String, dynamic>? container) {
  if (!containerHasError(container)) return null;
  return containerLastError(container) ?? 'container failed';
}
