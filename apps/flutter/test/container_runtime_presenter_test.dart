import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/containers/container_runtime_presenter.dart';
import 'package:prodavan/l10n/app_localizations_ru.dart';

void main() {
  test('formatContainerStateValue uses observed_state not db status', () {
    final l10n = AppLocalizationsRu();
    final text = formatContainerStateValue(
      const {
        'status': 'active',
        'observed_state': 'starting',
        'runtime': {'status': 'running', 'phase': 'Running'},
      },
      l10n,
    );
    expect(text, l10n.containerObservedStarting);
    expect(text, isNot('running'));
  });

  test('formatContainerStateValue localizes running', () {
    final l10n = AppLocalizationsRu();
    expect(
      formatContainerStateValue(const {'observed_state': 'running'}, l10n),
      'Запущен',
    );
  });

  test('formatContainerRestarts reads runtime restarts', () {
    final l10n = AppLocalizationsRu();
    expect(
      formatContainerRestarts(const {'runtime': {'restarts': 3}}, l10n),
      '3',
    );
  });

  test('formatContainerLastLaunch uses last_started_at fallback', () {
    final l10n = AppLocalizationsRu();
    final ts = DateTime.utc(2026, 8, 29, 12, 0).toIso8601String();
    expect(
      formatContainerLastLaunch({'runtime': {'last_started_at': ts}}, l10n),
      isNot(l10n.commonEmDash),
    );
  });

  test('formatContainerUptime from started_at', () {
    final l10n = AppLocalizationsRu();
    final started = DateTime.now().toUtc().subtract(const Duration(hours: 2, minutes: 5));
    final text = formatContainerUptime(
      {'runtime': {'started_at': started.toIso8601String()}},
      l10n,
    );
    expect(text, contains('2'));
    expect(text, contains('5'));
  });

  test('containerRuntimeHealthy only when observed_state running', () {
    expect(
      containerRuntimeHealthy(const {
        'status': 'active',
        'observed_state': 'running',
        'runtime': {'status': 'provisioning'},
      }),
      isTrue,
    );
    expect(
      containerRuntimeHealthy(const {
        'status': 'active',
        'runtime': {'status': 'running', 'phase': 'Running'},
      }),
      isFalse,
    );
  });

  test('containerShowResourceStatTiles false when paused', () {
    expect(
      containerShowResourceStatTiles(const {
        'status': 'active',
        'observed_state': 'paused',
        'runtime': {'metrics_available': true},
      }),
      isFalse,
    );
    expect(
      containerShowResourceStatTiles(const {
        'status': 'paused',
        'runtime': {'metrics_available': true},
      }),
      isFalse,
    );
    expect(
      containerShowResourceStatTiles(const {
        'status': 'active',
        'observed_state': 'running',
        'runtime': {'metrics_available': true, 'metrics': {'cpu_millicores': 100}},
      }),
      isTrue,
    );
  });

  test('containerShowStorageInLifecycle true when paused or error or metrics unavailable', () {
    expect(
      containerShowStorageInLifecycle(const {'observed_state': 'paused'}),
      isTrue,
    );
    expect(
      containerShowStorageInLifecycle(const {
        'observed_state': 'running',
        'runtime': {'metrics_available': false},
      }),
      isTrue,
    );
    expect(
      containerShowStorageInLifecycle(const {
        'observed_state': 'failed',
        'runtime': {'last_error': 'pod crash'},
      }),
      isTrue,
    );
    expect(
      containerShowStorageInLifecycle(const {
        'observed_state': 'running',
        'runtime': {'metrics_available': true, 'metrics': {'cpu_millicores': 1}},
      }),
      isFalse,
    );
  });

  test('containerIsInFlight covers pulling and provisioning', () {
    expect(containerIsInFlight(const {'observed_state': 'pulling'}), isTrue);
    expect(containerIsInFlight(const {'observed_state': 'provisioning'}), isTrue);
    expect(containerIsInFlight(const {'observed_state': 'running'}), isFalse);
    expect(
      containerIsInFlight(const {
        'runtime': {'observed_state': 'hydrating'},
      }),
      isTrue,
    );
  });

  test('formatContainerStateValue localizes pulling', () {
    final l10n = AppLocalizationsRu();
    expect(
      formatContainerStateValue(const {'observed_state': 'pulling'}, l10n),
      l10n.containerObservedPulling,
    );
  });

  test('formatContainerLaunchType localizes warm/cold, null otherwise', () {
    final l10n = AppLocalizationsRu();
    expect(formatContainerLaunchType('warm', l10n), l10n.containerLaunchTypeWarm);
    expect(formatContainerLaunchType('cold', l10n), l10n.containerLaunchTypeCold);
    expect(formatContainerLaunchType(null, l10n), isNull);
    expect(formatContainerLaunchType('bogus', l10n), isNull);
  });

  test('containerRuntimeRefName prefers sandbox name, then claim, then k8s', () {
    expect(
      containerRuntimeRefName(const {
        'runtime': {
          'sandbox_name': 'sb-1',
          'claim_name': 'cl-1',
          'k8s_pod_name': 'pod-1',
        },
      }),
      'sb-1',
    );
    expect(
      containerRuntimeRefName(const {
        'runtime': {'claim_name': 'cl-1'},
      }),
      'cl-1',
    );
    expect(
      containerRuntimeRefName(const {
        'runtime': {'k8s_pod_name': 'pod-1'},
      }),
      'pod-1',
    );
    expect(containerRuntimeRefName(const {'runtime': {}}), isNull);
  });

  test('ContainerRuntime parses sandbox identity fields', () {
    final rt = ContainerRuntime.fromJson(const {
      'runtime': {
        'sandbox_name': 'sb-1',
        'claim_name': 'cl-1',
        'service_fqdn': 'sb-1.sandboxes.svc.cluster.local',
        'launch_type': 'warm',
      },
    });
    expect(rt.sandboxName, 'sb-1');
    expect(rt.claimName, 'cl-1');
    expect(rt.serviceFqdn, 'sb-1.sandboxes.svc.cluster.local');
    expect(rt.launchType, 'warm');
    expect(containerIsSandboxRuntime(const {
      'runtime': {'sandbox_name': 'sb-1'},
    }), isTrue);
    expect(containerIsSandboxRuntime(const {
      'runtime': {'k8s_pod_name': 'pod-1'},
    }), isFalse);
  });

  test('ObservedState.parse normalizes legacy paused to suspended', () {
    expect(ObservedState.parse('paused'), ObservedState.suspended);
    expect(ObservedState.parse(' PAUSED '), ObservedState.suspended);
    expect(ObservedState.parse('suspended'), ObservedState.suspended);
    expect(ObservedState.parse('nope'), ObservedState.unknown);
  });
}
