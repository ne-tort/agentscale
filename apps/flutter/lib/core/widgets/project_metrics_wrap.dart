import 'package:flutter/material.dart';

import 'package:prodavan/core/format/storage_format.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/stat_tile.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Read-only project metric tiles — project settings header.
class ProjectMetricsWrap extends StatelessWidget {
  const ProjectMetricsWrap({
    super.key,
    required this.metrics,
    this.showLastActivity = true,
  });

  final Map<String, dynamic>? metrics;
  final bool showLastActivity;

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
    final storageBytes = metrics?['storage_bytes'];
    final storageValue = storageBytes is num
        ? formatStorageGb(storageBytes)
        : _metric('storage_bytes');

    return Wrap(
      spacing: AppSpacing.sm,
      runSpacing: AppSpacing.sm,
      children: [
        StatTile(label: l10n.commonAgentTokens, value: _metric('agent_tokens_used')),
        StatTile(label: l10n.adminAgentMessages, value: _agentRequests()),
        StatTile(label: l10n.commonStorageBytes, value: storageValue),
        if (showLastActivity && metrics?['last_activity_at'] != null)
          StatTile(
            label: l10n.commonLastActivity,
            value: _metric('last_activity_at', fallback: l10n.commonEmDash),
            width: 200,
          ),
      ],
    );
  }
}
