import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/app_subscription_preference.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_radio.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/admin/ai_key_integration_type.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Table picker for project AI key — name, provider type, subscription, radio.
class ProjectAiKeySelectPage extends StatelessWidget {
  const ProjectAiKeySelectPage({
    super.key,
    required this.keys,
    this.selectedKeyId,
  });

  final List<Map<String, dynamic>> keys;
  final String? selectedKeyId;

  static Future<String?> push(
    BuildContext context, {
    required List<Map<String, dynamic>> keys,
    String? selectedKeyId,
  }) {
    return Navigator.of(context).push<String>(
      MaterialPageRoute(
        builder: (_) => ProjectAiKeySelectPage(
          keys: keys,
          selectedKeyId: selectedKeyId,
        ),
      ),
    );
  }

  String _typeLabel(AppLocalizations l10n, Map<String, dynamic> k) {
    final t = AiKeyIntegrationType.fromKey(
      provider: k['provider'] as String? ?? '',
      apiKind: k['api_kind'] as String? ?? '',
    );
    switch (t.id) {
      case 'cursor_sdk':
        return l10n.adminTypeCursorSdk;
      case 'codex_sdk':
        return l10n.adminTypeCodexSdk;
      case 'claude_agent_sdk':
        return l10n.adminTypeClaudeSdk;
      default:
        return l10n.adminTypeApiKey;
    }
  }

  String _subscriptionLabel(AppLocalizations l10n, Map<String, dynamic> k) {
    final raw = (k['next_renewal_at'] as String? ?? '').trim();
    if (raw.isEmpty) return l10n.commonEmDash;
    return formatSubscriptionDate(raw);
  }

  void _select(BuildContext context, String keyId) {
    Navigator.of(context).pop(keyId);
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final rows = keys.map((k) {
      final id = k['id'] as String;
      return AppEntityRow(
        id: id,
        title: k['name'] as String? ?? id,
        cells: {
          'provider': _typeLabel(l10n, k),
          'subscription': _subscriptionLabel(l10n, k),
        },
        cellWidgets: {
          'select': AppRadio<String>(
            value: id,
            groupValue: selectedKeyId,
            onChanged: (_) => _select(context, id),
          ),
        },
      );
    }).toList();

    return AppScaffold(
      title: Text(l10n.projectPreferredAgentProvider),
      body: keys.isEmpty
          ? EmptyPlaceholder(title: l10n.commonEmpty)
          : Padding(
              padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
              child: AppEntityCollection(
                mode: AppEntityCollectionMode.table,
                rows: rows,
                primaryColumnLabel: l10n.adminKey,
                columns: [
                  AppEntityColumn(
                    id: 'provider',
                    label: l10n.projectAiKeyColumnProvider,
                  ),
                  AppEntityColumn(
                    id: 'subscription',
                    label: l10n.projectAiKeyColumnSubscription,
                  ),
                  AppEntityColumn(
                    id: 'select',
                    label: '',
                    width: 48,
                    align: AppEntityColumnAlign.center,
                  ),
                ],
                onOpen: (row) => _select(context, row.id),
              ),
            ),
    );
  }
}
