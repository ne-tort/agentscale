import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/stat_tile.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Read-only chat/session metric tiles — tokens + requests (no storage).
class SessionMetricsWrap extends StatelessWidget {
  const SessionMetricsWrap({
    super.key,
    required this.metrics,
  });

  final Map<String, dynamic>? metrics;

  String _metric(String key, {String fallback = '0'}) {
    final v = metrics?[key];
    if (v == null) return fallback;
    return '$v';
  }

  String _agentRequests() {
    final v = metrics?['agent_requests'] ?? metrics?['agent_messages'];
    if (v == null) return '0';
    return '$v';
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return Wrap(
      spacing: AppSpacing.sm,
      runSpacing: AppSpacing.sm,
      children: [
        StatTile(
          label: l10n.commonAgentTokens,
          value: _metric('agent_tokens_used'),
        ),
        StatTile(
          label: l10n.adminAgentMessages,
          value: _agentRequests(),
        ),
      ],
    );
  }
}
