import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/widgets/container_metrics_wrap.dart';
import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/l10n/app_localizations.dart';

Widget themed(Widget home, {Locale locale = const Locale('en')}) {
  return MaterialApp(
    theme: AppTheme.light,
    locale: locale,
    supportedLocales: AppLocalizations.supportedLocales,
    localizationsDelegates: const [
      AppLocalizations.delegate,
      GlobalMaterialLocalizations.delegate,
      GlobalWidgetsLocalizations.delegate,
      GlobalCupertinoLocalizations.delegate,
    ],
    home: Scaffold(body: SingleChildScrollView(child: home)),
  );
}

void main() {
  testWidgets('project metrics response renders CPU and RAM history with latest values', (tester) async {
    await tester.pumpWidget(
      themed(
        const ContainerMetricsWrap(
          container: {'observed_state': 'running', 'runtime': {'metrics_available': true}},
          runtimeMetrics: {
            'window': '1h',
            'series': [
              {'timestamp': '2026-01-01T00:00:00Z', 'cpu_millicores': 120, 'memory_bytes': 1048576},
              {'timestamp': '2026-01-01T00:05:00Z', 'cpu_millicores': 240, 'memory_bytes': 2097152},
            ],
          },
        ),
        locale: const Locale('en'),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('CPU'), findsOneWidget);
    expect(find.text('Memory'), findsOneWidget);
    expect(find.text('CPU 240m'), findsOneWidget);
    expect(find.text('Memory 2.0 MB'), findsOneWidget);
    expect(find.byType(CustomPaint), findsWidgets);
  });

  testWidgets('project metrics response without CPU/RAM samples shows localized empty state', (tester) async {
    await tester.pumpWidget(
      themed(
        const ContainerMetricsWrap(
          container: {'observed_state': 'running'},
          runtimeMetrics: {'window': '1h', 'series': [{'timestamp': '2026-01-01T00:00:00Z'}]},
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('CPU —'), findsNothing);
    expect(find.text('Memory —'), findsNothing);
    expect(find.text('No CPU or memory samples yet'), findsOneWidget);
  });

  testWidgets('project metrics accepts wrapped series and latest fallback', (tester) async {
    await tester.pumpWidget(
      themed(
        const ContainerMetricsWrap(
          container: {'observed_state': 'running'},
          runtimeMetrics: {
            'series': {
              'data': [
                {'cpu': '80', 'memoryBytes': 1048576},
              ],
            },
          },
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('CPU 80m'), findsOneWidget);
    expect(find.text('Memory 1.0 MB'), findsOneWidget);
  });

  testWidgets('project metrics falls back to latest sample when series is empty', (tester) async {
    await tester.pumpWidget(
      themed(
        const ContainerMetricsWrap(
          container: {'observed_state': 'running'},
          runtimeMetrics: {
            'series': <Object>[],
            'latest': {'cpu_millicores': 60, 'memory_bytes': 524288},
          },
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('CPU 60m'), findsOneWidget);
    expect(find.text('Memory 512.0 KB'), findsOneWidget);
  });

  testWidgets('project metrics error is localized', (tester) async {
    await tester.pumpWidget(
      themed(
        const ContainerMetricsWrap(
          container: {'observed_state': 'running'},
          runtimeMetricsError: true,
        ),
        locale: const Locale('ru'),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Не удалось загрузить метрики'), findsOneWidget);
  });

  testWidgets('sandbox runtime row shows ref, warm badge and fqdn tooltip', (tester) async {
    await tester.pumpWidget(
      themed(
        const ContainerMetricsWrap(
          container: {
            'observed_state': 'running',
            'runtime': {
              'observed_state': 'running',
              'sandbox_name': 'sb-abc123',
              'claim_name': 'claim-xyz',
              'launch_type': 'warm',
              'service_fqdn': 'sb-abc123.sandboxes.svc.cluster.local',
              'metrics_available': true,
              'metrics': {'cpu_millicores': 100},
            },
          },
        ),
        locale: const Locale('ru'),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Рантайм'), findsOneWidget);
    expect(find.text('sb-abc123'), findsOneWidget);
    expect(find.text('Тёплый старт'), findsOneWidget);
    expect(
      find.byWidgetPredicate(
        (w) => w is Tooltip && w.message == 'sb-abc123.sandboxes.svc.cluster.local',
      ),
      findsOneWidget,
    );
  });

  testWidgets('cold launch renders the cold badge (en)', (tester) async {
    await tester.pumpWidget(
      themed(
        const ContainerMetricsWrap(
          container: {
            'observed_state': 'running',
            'runtime': {
              'observed_state': 'running',
              'sandbox_name': 'sb-cold',
              'launch_type': 'cold',
              'metrics_available': true,
              'metrics': {'cpu_millicores': 50},
            },
          },
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Runtime'), findsOneWidget);
    expect(find.text('sb-cold'), findsOneWidget);
    expect(find.text('Cold start'), findsOneWidget);
  });

  testWidgets('legacy k8s container has no Runtime row (Kub ID stays)', (tester) async {
    await tester.pumpWidget(
      themed(
        const ContainerMetricsWrap(
          container: {
            'observed_state': 'running',
            'runtime': {
              'observed_state': 'running',
              'k8s_pod_name': 'pod-legacy-1',
              'metrics_available': true,
              'metrics': {'cpu_millicores': 10},
            },
          },
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Runtime'), findsNothing);
    expect(find.text('pod-legacy-1'), findsOneWidget);
  });

  testWidgets('sandbox without launch_type shows ref without badge', (tester) async {
    await tester.pumpWidget(
      themed(
        const ContainerMetricsWrap(
          container: {
            'observed_state': 'suspended',
            'runtime': {
              'observed_state': 'suspended',
              'claim_name': 'claim-only',
            },
          },
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Runtime'), findsOneWidget);
    expect(find.text('claim-only'), findsOneWidget);
    expect(find.text('Warm start'), findsNothing);
    expect(find.text('Cold start'), findsNothing);
  });
}
