import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/containers/container_runtime_presenter.dart';
import 'package:prodavan/l10n/app_localizations_ru.dart';

void main() {
  test('formatContainerRuntimeCell uses observed_state not db status', () {
    final l10n = AppLocalizationsRu();
    final text = formatContainerRuntimeCell(
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

  test('formatContainerRuntimeDetail shows observed state and awaiting metrics', () {
    final l10n = AppLocalizationsRu();
    final text = formatContainerRuntimeDetail(
      const {
        'status': 'active',
        'observed_state': 'starting',
        'k8s_phase': 'Running',
        'runtime': {
          'observed_state': 'starting',
          'phase': 'Running',
          'orchestrator_status': 'provisioning',
          'desired_state': 'running',
          'ready': true,
        },
      },
      l10n,
    );
    expect(text, contains(l10n.containerObservedStarting));
    expect(text, contains(l10n.containerMetricsAwaiting));
    expect(text, contains('provisioning'));
  });

  test('formatContainerRuntimeDetail shows metrics when running verified', () {
    final l10n = AppLocalizationsRu();
    final text = formatContainerRuntimeDetail(
      {
        'status': 'active',
        'observed_state': 'running',
        'runtime': {
          'observed_state': 'running',
          'phase': 'Running',
          'metrics': {'cpu_millicores': 12, 'memory_bytes': 67108864},
        },
      },
      l10n,
    );
    expect(text, contains('12m'));
    expect(text, contains('64.0 MiB'));
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
