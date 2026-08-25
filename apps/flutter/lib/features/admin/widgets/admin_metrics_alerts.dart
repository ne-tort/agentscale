import 'package:flutter/material.dart';

import 'package:prodavan/core/widgets/inline_error_banner.dart';
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

  static List<String> messagesFor(Map<String, dynamic>? metrics, AppLocalizations l10n) {
    if (metrics == null) return const [];
    final out = <String>[];
    final expiringKeys = asInt(metrics['ai_keys_expiring_soon']);
    if (expiringKeys > 0) {
      final next = metrics['next_key_renewal_at'];
      out.add(
        next != null
            ? l10n.adminMetricsAiKeysRenewSoonNext('$expiringKeys', '$next')
            : l10n.adminMetricsAiKeysRenewSoon('$expiringKeys'),
      );
    }
    if (asInt(metrics['employees_total']) > 0 && asInt(metrics['ai_keys_bound']) == 0) {
      out.add(l10n.adminMetricsNoAiKeysBound);
    }
    if (metrics['high_agent_usage'] == true) {
      out.add(l10n.adminMetricsHighTokenUsage('${metrics['agent_tokens_used'] ?? '?'}'));
    }
    final lifetime = metrics['subscription_lifetime'] == true;
    if (!lifetime && metrics['subscription_expired'] == true) {
      out.add(l10n.adminMetricsSubscriptionExpired);
    }
    if (!lifetime && metrics['subscription_expiring_soon'] == true) {
      out.add(
        l10n.adminMetricsSubscriptionExpiring('${metrics['subscription_ends_at'] ?? l10n.commonEmDash}'),
      );
    }
    return out;
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final messages = messagesFor(metrics, l10n);
    if (messages.isEmpty) return const SizedBox.shrink();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (final msg in messages) ...[
          InlineErrorBanner(message: msg),
          const SizedBox(height: 8),
        ],
      ],
    );
  }
}
