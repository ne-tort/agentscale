import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/containers/project_container_poll.dart';

class _SeqApi extends Fake implements ProdavanApi {
  _SeqApi(this.states);

  final List<Map<String, dynamic>> states;
  int calls = 0;

  @override
  Future<Map<String, dynamic>> getProjectContainer(String projectId) async {
    final i = calls < states.length ? calls : states.length - 1;
    calls += 1;
    return Map<String, dynamic>.from(states[i]);
  }
}

void main() {
  group('containerObservedSettled waitFor semantics', () {
    test('suspended is NOT settled for waitForRunning (resume wake trap)', () {
      const suspended = {'observed_state': 'suspended'};
      expect(
        containerObservedSettled(suspended, waitFor: containerPollWaitForRunning),
        isFalse,
      );
      expect(
        containerObservedSettled(
          const {'observed_state': 'running'},
          waitFor: containerPollWaitForRunning,
        ),
        isTrue,
      );
      expect(
        containerObservedSettled(
          const {'observed_state': 'failed'},
          waitFor: containerPollWaitForRunning,
        ),
        isTrue,
      );
      // Default (no waitFor) still treats suspended as terminal — the trap
      // the wake/resume call-sites must avoid by passing waitFor explicitly.
      expect(containerObservedSettled(suspended), isTrue);
    });

    test('legacy paused wire value normalizes to suspended', () {
      const paused = {'observed_state': 'paused'};
      expect(
        containerObservedSettled(paused, waitFor: containerPollWaitForSuspended),
        isTrue,
      );
      expect(
        containerObservedSettled(paused, waitFor: containerPollWaitForRunning),
        isFalse,
      );
    });

    test('waitForSuspended settles only on sleeping/gone states', () {
      expect(
        containerObservedSettled(
          const {'observed_state': 'suspended'},
          waitFor: containerPollWaitForSuspended,
        ),
        isTrue,
      );
      expect(
        containerObservedSettled(
          const {'observed_state': 'absent'},
          waitFor: containerPollWaitForSuspended,
        ),
        isTrue,
      );
      expect(
        containerObservedSettled(
          const {'observed_state': 'failed'},
          waitFor: containerPollWaitForSuspended,
        ),
        isTrue,
      );
      expect(
        containerObservedSettled(
          const {'observed_state': 'running'},
          waitFor: containerPollWaitForSuspended,
        ),
        isFalse,
      );
    });

    test('nested runtime.observed_state is respected with waitFor', () {
      expect(
        containerObservedSettled(
          const {
            'runtime': {'observed_state': 'suspended'},
          },
          waitFor: containerPollWaitForRunning,
        ),
        isFalse,
      );
      expect(
        containerObservedSettled(
          const {
            'runtime': {'observed_state': 'running'},
          },
          waitFor: containerPollWaitForRunning,
        ),
        isTrue,
      );
    });
  });

  group('pollProjectContainerUntilSettled', () {
    test('waitFor running keeps polling through suspended ticks', () async {
      final api = _SeqApi(const [
        {'observed_state': 'suspended'},
        {'observed_state': 'suspended'},
        {'observed_state': 'running'},
      ]);
      final result = await pollProjectContainerUntilSettled(
        api: api,
        projectId: 'prj',
        interval: const Duration(milliseconds: 5),
        timeout: const Duration(seconds: 5),
        waitFor: containerPollWaitForRunning,
      );
      expect(result?['observed_state'], 'running');
      expect(api.calls, 3);
    });

    test('default waitFor stops at the first suspended tick', () async {
      final api = _SeqApi(const [
        {'observed_state': 'suspended'},
        {'observed_state': 'running'},
      ]);
      final result = await pollProjectContainerUntilSettled(
        api: api,
        projectId: 'prj',
        interval: const Duration(milliseconds: 5),
        timeout: const Duration(seconds: 5),
      );
      expect(result?['observed_state'], 'suspended');
      expect(api.calls, 1);
    });

    test('returns the last (unsettled) item after the timeout', () async {
      final api = _SeqApi(const [
        {'observed_state': 'provisioning'},
      ]);
      final result = await pollProjectContainerUntilSettled(
        api: api,
        projectId: 'prj',
        interval: const Duration(milliseconds: 5),
        timeout: const Duration(milliseconds: 30),
        waitFor: containerPollWaitForRunning,
      );
      expect(result?['observed_state'], 'provisioning');
      expect(
        containerObservedSettled(result, waitFor: containerPollWaitForRunning),
        isFalse,
      );
    });
  });
}
