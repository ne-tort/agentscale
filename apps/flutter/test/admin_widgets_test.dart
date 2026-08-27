import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/features/admin/admin_shell.dart';
import 'package:prodavan/features/admin/widgets/admin_metrics_alerts.dart';
import 'package:prodavan/l10n/app_localizations.dart';

Widget _enApp(Widget home) {
  return MaterialApp(
    theme: AppTheme.light,
    locale: const Locale('en'),
    supportedLocales: AppLocalizations.supportedLocales,
    localizationsDelegates: const [
      AppLocalizations.delegate,
      GlobalMaterialLocalizations.delegate,
      GlobalWidgetsLocalizations.delegate,
      GlobalCupertinoLocalizations.delegate,
    ],
    home: home,
  );
}

void main() {
  late AppLocalizations l10n;

  setUpAll(() async {
    l10n = await AppLocalizations.delegate.load(const Locale('en'));
  });

  test('messagesFor empty when metrics null', () {
    expect(AdminMetricsAlerts.messagesFor(null, l10n), isEmpty);
  });

  test('messagesFor subscription expired and no keys', () {
    final msgs = AdminMetricsAlerts.messagesFor({
      'employees_total': 2,
      'ai_keys_bound': 0,
      'subscription_lifetime': false,
      'subscription_expired': true,
    }, l10n);
    expect(msgs, contains(l10n.adminMetricsNoAiKeysBound));
    expect(msgs, contains(l10n.adminMetricsSubscriptionExpired));
  });

  test('messagesFor ignores identity unbound on company banners', () {
    final msgs = AdminMetricsAlerts.messagesFor({
      'keycloak_unbound': true,
      'employees_keycloak_unbound': 3,
      'subscription_lifetime': true,
    }, l10n);
    expect(msgs, isEmpty);
  });

  test('messagesFor ignores expired when lifetime', () {
    final msgs = AdminMetricsAlerts.messagesFor({
      'subscription_lifetime': true,
      'subscription_expired': true,
      'subscription_expiring_soon': true,
    }, l10n);
    expect(msgs, isEmpty);
  });

  testWidgets('renders banners from metrics', (tester) async {
    await tester.pumpWidget(
      _enApp(
        Scaffold(
          body: AdminMetricsAlerts(
            metrics: {
              'high_agent_usage': true,
              'agent_tokens_used': 90000,
              'subscription_lifetime': false,
              'subscription_expiring_soon': true,
              'subscription_ends_at': '2026-09-01',
            },
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.textContaining('High agent token usage'), findsOneWidget);
    expect(find.textContaining('Subscription expiring'), findsOneWidget);
  });

  test('messagesFor ai keys expiring', () {
    final msgs = AdminMetricsAlerts.messagesFor({
      'ai_keys_expiring_soon': 2,
      'next_key_renewal_at': '2026-09-01',
      'employees_total': 1,
      'ai_keys_bound': 1,
      'subscription_lifetime': true,
    }, l10n);
    expect(msgs, contains(l10n.adminMetricsAiKeysRenewSoonNext('2', '2026-09-01')));
  });

  testWidgets('admin shell navigation destinations', (tester) async {
    // Wide surface → left NavigationRail (all sections).
    await tester.pumpWidget(_enApp(const AdminShell()));
    await tester.pumpAndSettle();
    expect(find.byType(NavigationRail), findsOneWidget);
    expect(find.text('Overview'), findsWidgets);
    expect(find.text('Companies'), findsWidgets);
    expect(find.text('AI Keys'), findsWidgets);
    expect(find.text('Projects'), findsWidgets);
    expect(find.text('Cabinets'), findsWidgets);
    expect(find.text('Modules'), findsWidgets);
    expect(find.text('Prodavan'), findsWidgets);
    expect(find.text('Management'), findsNothing);

    // Phone-width surface → bottom bar: Overview, Management, Settings.
    tester.view.physicalSize = const Size(390, 800);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(_enApp(const AdminShell()));
    await tester.pumpAndSettle();
    expect(find.byType(NavigationBar), findsOneWidget);
    final bar = tester.widget<NavigationBar>(find.byType(NavigationBar));
    final labels = bar.destinations
        .map((d) => (d as NavigationDestination).label)
        .toList();
    expect(labels, ['Overview', 'Management', 'Settings']);
    expect(find.text('Companies'), findsNothing);

    await tester.tap(find.text('Management'));
    await tester.pumpAndSettle();
    expect(find.text('Companies'), findsOneWidget);
    expect(find.text('AI Keys'), findsOneWidget);
    expect(find.text('Projects'), findsOneWidget);
    expect(find.text('Cabinets'), findsOneWidget);
    expect(find.text('Modules'), findsOneWidget);

    await tester.tap(find.text('Settings'));
    await tester.pumpAndSettle();
    expect(find.text('Language'), findsOneWidget);

    await tester.tap(find.text('Management'));
    await tester.pumpAndSettle();
    expect(find.text('Companies'), findsOneWidget);
  });
}
