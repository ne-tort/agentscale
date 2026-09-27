import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/containers/container_runtime_presenter.dart';

/// Terminal observed states: the poll stops once the sandbox settles into
/// one of these (agent-sandbox conditions reach a stable value).
const _terminalObservedStates = {
  'running',
  'failed',
  'suspended',
  'absent',
};

/// Target states for launch/reload/resume polls: wait until the pod is live
/// or definitively dead. `suspended` is deliberately excluded — during resume
/// the sandbox still reports `suspended` on the first ticks while it is
/// waking up, so treating it as settled ends the poll immediately and
/// freezes the wake UX.
const containerPollWaitForRunning = {'running', 'failed'};

/// Target states for pause polls: the sandbox went to sleep (or died/gone).
const containerPollWaitForSuspended = {'suspended', 'failed', 'absent'};

/// Launch/reload/resume poll ended in a failed observed state — [reason]
/// carries the container's last_error for an honest, prefixed snack.
class ContainerObservedFailureException implements Exception {
  ContainerObservedFailureException(this.reason);

  final String reason;

  @override
  String toString() => 'ContainerObservedFailureException: $reason';
}

bool containerObservedSettled(Map<String, dynamic>? item, {Set<String>? waitFor}) {
  final runtime = item?['runtime'];
  final merged = <String, dynamic>{
    if (item != null) ...item,
    if (runtime is Map) 'runtime': runtime,
  };
  var state = merged['observed_state'] as String? ??
      (runtime is Map ? runtime['observed_state'] as String? : null);
  if (state == null) return false;
  state = state.trim();
  if (state.isEmpty) return false;
  // Legacy wire value: pre agent-sandbox payloads used `paused`.
  if (state == 'paused') state = 'suspended';
  return (waitFor ?? _terminalObservedStates).contains(state);
}

/// agent-sandbox suspend/resume is seconds-scale — 5 min is plenty
/// (warm claim ~2s, cold start with pull is the long tail).
/// Launch keeps its own 12-min budget (appJobDefaultTimeout): cold image
/// pulls are the long tail there.
const containerPollDefaultTimeout = Duration(minutes: 5);

/// Long-poll ceiling: back off from the initial 1s tick up to 5s so a cold
/// image pull does not hammer the API with 1s requests for minutes.
const _pollMaxInterval = Duration(seconds: 5);

/// Poll project container until observed_state settles into [waitFor]
/// (default: any terminal state) or [timeout] elapses.
Future<Map<String, dynamic>?> pollProjectContainerUntilSettled({
  required ProdavanApi api,
  required String projectId,
  Duration interval = const Duration(seconds: 1),
  Duration timeout = containerPollDefaultTimeout,
  Set<String>? waitFor,
  void Function(Map<String, dynamic>? item)? onTick,
}) async {
  final deadline = DateTime.now().add(timeout);
  var delay = interval;
  Map<String, dynamic>? last;
  while (DateTime.now().isBefore(deadline)) {
    last = await api.getProjectContainer(projectId);
    onTick?.call(last);
    if (containerObservedSettled(last, waitFor: waitFor)) return last;
    await Future<void>.delayed(delay);
    if (delay < _pollMaxInterval) {
      delay = delay * 2;
      if (delay > _pollMaxInterval) delay = _pollMaxInterval;
    }
  }
  return last;
}

String? containerObservedFailureMessage(Map<String, dynamic>? container) {
  if (!containerHasError(container)) return null;
  return containerLastError(container) ?? 'container failed';
}
