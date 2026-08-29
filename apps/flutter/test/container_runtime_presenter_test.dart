import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/containers/container_runtime_presenter.dart';
import 'package:prodavan/l10n/app_localizations_ru.dart';

void main() {
  test('formatContainerRuntimeCell shows not running for active project without pod', () {
    final l10n = AppLocalizationsRu();
    final text = formatContainerRuntimeCell(
      const {'status': 'active', 'runtime': null},
      l10n,
    );
    expect(text, l10n.adminContainerRuntimeNotStartedShort);
  });

  test('formatContainerRuntimeDetail shows phase and metrics', () {
    final l10n = AppLocalizationsRu();
    final text = formatContainerRuntimeDetail(
      {
        'status': 'active',
        'k8s_phase': 'Running',
        'runtime': {
          'status': 'running',
          'desired_state': 'running',
          'phase': 'Running',
          'ready': true,
          'restarts': 0,
          'metrics': {'cpu_millicores': 12, 'memory_bytes': 67108864},
        },
      },
      l10n,
    );
    expect(text, contains('Running'));
    expect(text, contains('12m'));
    expect(text, contains('64.0 MiB'));
  });

  test('formatContainerRuntimeDetail shows last_error for error without runtime', () {
    final l10n = AppLocalizationsRu();
    final text = formatContainerRuntimeDetail(
      const {'status': 'error', 'last_error': 'k8s boom', 'runtime': null},
      l10n,
    );
    expect(text, contains('k8s boom'));
  });

  test('containerRuntimeHealthy false for failed pod', () {
    expect(
      containerRuntimeHealthy(const {
        'status': 'active',
        'runtime': {'status': 'failed', 'last_error': 'boom'},
      }),
      isFalse,
    );
    expect(
      containerRuntimeHealthy(const {
        'status': 'active',
        'runtime': {'status': 'running', 'phase': 'Running'},
      }),
      isTrue,
    );
  });
}