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
}
