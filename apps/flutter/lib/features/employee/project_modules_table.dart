import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_switch.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Project modules table — name, profile, enable checkbox.
class ProjectModulesTable extends StatelessWidget {
  const ProjectModulesTable({
    super.key,
    required this.modules,
    required this.onOpen,
    required this.onEnabledChanged,
    this.showHeader = true,
  });

  final List<Map<String, dynamic>> modules;
  final ValueChanged<Map<String, dynamic>> onOpen;
  final Future<void> Function(String moduleId, bool enabled) onEnabledChanged;
  final bool showHeader;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final rows = modules.map((m) {
      final id = m['module_id'] as String;
      final hasProfiles = m['has_profiles'] == true;
      final profileName = m['profile_name'] as String?;
      return AppEntityRow(
        id: id,
        title: m['name'] as String? ?? id,
        cells: {
          'profile': hasProfiles ? (profileName ?? l10n.commonEmDash) : l10n.commonEmDash,
        },
        cellWidgets: {
          'enabled': AppSwitch(
            value: m['enabled'] == true,
            onChanged: (v) => onEnabledChanged(id, v),
          ),
        },
      );
    }).toList();

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (showHeader)
          Padding(
            padding: const EdgeInsets.only(left: AppSpacing.md, bottom: AppSpacing.sm),
            child: Text(
              l10n.projectModulesLabel,
              style: Theme.of(context).textTheme.titleMedium,
            ),
          ),
        SizedBox(
          height: (rows.length * 48.0).clamp(120, 360),
          child: AppEntityCollection(
            mode: AppEntityCollectionMode.table,
            rows: rows,
            primaryColumnLabel: l10n.commonName,
            columns: [
              AppEntityColumn(id: 'profile', label: l10n.projectModuleProfileColumn),
              AppEntityColumn(
                id: 'enabled',
                label: '',
                width: 56,
                align: AppEntityColumnAlign.center,
              ),
            ],
            onOpen: (row) {
              final mod = modules.firstWhere((m) => m['module_id'] == row.id);
              onOpen(mod);
            },
          ),
        ),
      ],
    );
  }
}
