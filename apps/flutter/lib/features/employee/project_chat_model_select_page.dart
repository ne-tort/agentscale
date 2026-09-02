import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_radio.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Table picker for project chat model — metadata from catalog enrichment.
class ProjectChatModelSelectPage extends StatelessWidget {
  const ProjectChatModelSelectPage({
    super.key,
    required this.models,
    this.selectedModelId,
    this.enabled = true,
  });

  final List<Map<String, dynamic>> models;
  final String? selectedModelId;
  final bool enabled;

  static Future<String?> push(
    BuildContext context, {
    required List<Map<String, dynamic>> models,
    String? selectedModelId,
    bool enabled = true,
  }) {
    return Navigator.of(context).push<String>(
      MaterialPageRoute(
        builder: (_) => ProjectChatModelSelectPage(
          models: models,
          selectedModelId: selectedModelId,
          enabled: enabled,
        ),
      ),
    );
  }

  String _priceLabel(AppLocalizations l10n, Map<String, dynamic> m) {
    final input = m['input_price_usd_per_mtok'];
    final output = m['output_price_usd_per_mtok'];
    if (input == null && output == null) return l10n.commonEmDash;
    String fmt(Object? v) => v == null ? '—' : '\$$v';
    return '${fmt(input)} / ${fmt(output)}';
  }

  String _tokensLabel(AppLocalizations l10n, Map<String, dynamic> m) {
    final raw = m['max_context_tokens'];
    if (raw == null) return l10n.commonEmDash;
    return '$raw';
  }

  String _publisherLabel(AppLocalizations l10n, Map<String, dynamic> m) {
    final raw = (m['publisher'] as String? ?? '').trim();
    return raw.isEmpty ? l10n.commonEmDash : raw;
  }

  String _releasedLabel(AppLocalizations l10n, Map<String, dynamic> m) {
    final raw = (m['released_at'] as String? ?? '').trim();
    return raw.isEmpty ? l10n.commonEmDash : raw;
  }

  void _select(BuildContext context, String modelId) {
    if (!enabled) return;
    Navigator.of(context).pop(modelId);
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final rows = models.map((m) {
      final id = m['id'] as String? ?? m['label'] as String? ?? '';
      return AppEntityRow(
        id: id,
        title: m['label'] as String? ?? id,
        cells: {
          'price': _priceLabel(l10n, m),
          'tokens': _tokensLabel(l10n, m),
          'publisher': _publisherLabel(l10n, m),
          'released': _releasedLabel(l10n, m),
        },
        cellWidgets: {
          'select': AppRadio<String>(
            value: id,
            groupValue: selectedModelId,
            onChanged: enabled ? (_) => _select(context, id) : null,
          ),
        },
      );
    }).toList();

    return AppScaffold(
      title: Text(l10n.projectChatModelSelectTitle),
      body: models.isEmpty
          ? EmptyPlaceholder(
              title: l10n.projectChatModelLabel,
              icon: Icons.smart_toy_outlined,
            )
          : Padding(
              padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
              child: AppEntityCollection(
                mode: AppEntityCollectionMode.table,
                rows: rows,
                primaryColumnLabel: l10n.projectChatModelLabel,
                columns: [
                  AppEntityColumn(id: 'price', label: l10n.projectChatModelColumnPrice),
                  AppEntityColumn(id: 'tokens', label: l10n.projectChatModelColumnMaxTokens),
                  AppEntityColumn(id: 'publisher', label: l10n.projectChatModelColumnPublisher),
                  AppEntityColumn(id: 'released', label: l10n.projectChatModelColumnReleased),
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
