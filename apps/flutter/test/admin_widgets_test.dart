import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/features/admin/admin_shell.dart';
import 'package:prodavan/features/admin/widgets/admin_metrics_alerts.dart';

void main() {
  test('messagesFor empty when metrics null', () {
    expect(AdminMetricsAlerts.messagesFor(null), isEmpty);
  });

  test('messagesFor subscription expired and no keys', () {
    final msgs = AdminMetricsAlerts.messagesFor({
      'employees_total': 2,
      'ai_keys_bound': 0,
      'subscription_lifetime': false,
      'subscription_expired': true,
    });
    expect(msgs, contains('No AI keys bound — agent will return NO_AI_KEY'));
    expect(msgs, contains('Subscription expired'));
  });

  test('messagesFor ignores expired when lifetime', () {
    final msgs = AdminMetricsAlerts.messagesFor({
      'subscription_lifetime': true,
      'subscription_expired': true,
      'subscription_expiring_soon': true,
    });
    expect(msgs, isEmpty);
  });

  testWidgets('renders banners from metrics', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light,
        home: Scaffold(
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
    });
    expect(msgs, contains('2 AI key(s) renew soon · next 2026-09-01'));
  });

  testWidgets('admin shell navigation destinations', (tester) async {
    await tester.pumpWidget(MaterialApp(theme: AppTheme.light, home: const AdminShell()));
    await tester.pump(); // avoid waiting on overview network load
    expect(find.byType(NavigationBar), findsOneWidget);
    expect(find.byType(NavigationDestination), findsNWidgets(4));
    expect(find.text('Companies'), findsWidgets);
    expect(find.text('AI Keys'), findsWidgets);
    expect(find.text('Bundles'), findsWidgets);
  });
}
