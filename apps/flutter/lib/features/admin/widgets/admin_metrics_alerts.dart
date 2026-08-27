import 'package:flutter/material.dart';

import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Company metrics alert banners for Admin detail (L04).
class AdminMetricsAlerts extends StatelessWidget {
  const AdminMetricsAlerts({super.key, required this.metrics});

  final Map<String, dynamic>? metrics;

  static int asInt(dynamic value) {
    if (value is int) return value;
    if (value is num) return value.toInt();
    return int.tryParse(value?.toString() ?? '') ?? 0;
  }

  static List<(AppStatusSeverity, String)> alertsFor(
    Map<String, dynamic>? metrics,
    AppLocalizations l10n,
  ) {
    if (metrics == null) return const [];
    final out = <(AppStatusSeverity, String)>[];
    final expiringKeys = asInt(metrics['ai_keys_expiring_soon']);
    if (expiringKeys > 0) {
      final next = metrics['next_key_renewal_at'];
      out.add((
        AppStatusSeverity.warning,
        next != null
            ? l10n.adminMetricsAiKeysRenewSoonNext('$expiringKeys', '$next')
            : l10n.adminMetricsAiKeysRenewSoon('$expiringKeys'),
      ));
    }
    if (asInt(metrics['employees_total']) > 0 && asInt(metrics['ai_keys_bound']) == 0) {
      out.add((AppStatusSeverity.warning, l10n.adminMetricsNoAiKeysBound));
    }
    if (metrics['high_agent_usage'] == true) {
      out.add((
        AppStatusSeverity.warning,
        l10n.adminMetricsHighTokenUsage('${metrics['agent_tokens_used'] ?? '?'}'),
      ));
    }
    final lifetime = metrics['subscription_lifetime'] == true;
    if (!lifetime && metrics['subscription_expired'] == true) {
      out.add((AppStatusSeverity.error, l10n.adminMetricsSubscriptionExpired));
    }
    if (!lifetime && metrics['subscription_expiring_soon'] == true) {
      out.add((
        AppStatusSeverity.warning,
        l10n.adminMetricsSubscriptionExpiring(
          '${metrics['subscription_ends_at'] ?? l10n.commonEmDash}',
        ),
      ));
    }
    if (metrics['keycloak_unbound'] == true) {
      out.add((AppStatusSeverity.warning, l10n.adminMetricsIdentityUnbound));
    }
    final unboundEmployees = asInt(metrics['employees_keycloak_unbound']);
    if (unboundEmployees > 0) {
      out.add((
        AppStatusSeverity.warning,
        l10n.adminMetricsEmployeesUnbound('$unboundEmployees'),
      ));
    }
    return out;
  }

  /// Compatibility: message strings only (same order as [alertsFor]).
  static List<String> messagesFor(Map<String, dynamic>? metrics, AppLocalizations l10n) {
    return [for (final a in alertsFor(metrics, l10n)) a.$2];
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final alerts = alertsFor(metrics, l10n);
    if (alerts.isEmpty) return const SizedBox.shrink();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (final (severity, msg) in alerts) ...[
          AppStatusBanner(severity: severity, message: msg),
          const SizedBox(height: 8),
        ],
      ],
    );
  }
}
