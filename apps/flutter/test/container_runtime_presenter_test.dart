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
}
