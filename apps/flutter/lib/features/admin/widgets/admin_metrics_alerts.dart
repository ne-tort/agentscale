import 'package:flutter/material.dart';

import 'package:prodavan/core/widgets/inline_error_banner.dart';

/// Company metrics alert banners for Admin detail (L04).
class AdminMetricsAlerts extends StatelessWidget {
  const AdminMetricsAlerts({super.key, required this.metrics});

  final Map<String, dynamic>? metrics;

  static int asInt(dynamic value) {
    if (value is int) return value;
    if (value is num) return value.toInt();
    return int.tryParse(value?.toString() ?? '') ?? 0;
  }

  static List<String> messagesFor(Map<String, dynamic>? metrics) {
    if (metrics == null) return const [];
    final out = <String>[];
    final expiringKeys = asInt(metrics['ai_keys_expiring_soon']);
    if (expiringKeys > 0) {
      final next = metrics['next_key_renewal_at'];
      out.add(
        '$expiringKeys AI key(s) renew soon'
        '${next != null ? ' · next $next' : ''}',
      );
    }
    if (asInt(metrics['employees_total']) > 0 && asInt(metrics['ai_keys_bound']) == 0) {
      out.add('No AI keys bound — agent will return NO_AI_KEY');
    }
    if (metrics['high_agent_usage'] == true) {
      out.add('High agent token usage (${metrics['agent_tokens_used'] ?? '?'} tokens)');
    }
    final lifetime = metrics['subscription_lifetime'] == true;
    if (!lifetime && metrics['subscription_expired'] == true) {
      out.add('Subscription expired');
    }
    if (!lifetime && metrics['subscription_expiring_soon'] == true) {
      out.add('Subscription expiring · ${metrics['subscription_ends_at'] ?? '—'}');
    }
    return out;
  }

  @override
  Widget build(BuildContext context) {
    final messages = messagesFor(metrics);
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
