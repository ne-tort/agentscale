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
