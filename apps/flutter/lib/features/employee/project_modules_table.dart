import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Product modules that default to a global cabinet→project bind.
const _globalDefaultModuleIds = {'mod_prompts', 'mod_mcp', 'mod_files'};

/// Resolve effective MP bind kind for a project-modules list row.
String projectModuleBindKind(Map<String, dynamic> module) {
  final raw = module['bind_kind'];
  if (raw is String && (raw == 'local' || raw == 'global')) return raw;
  if (module['enabled'] != true) return '';
  final id = module['module_id'] as String? ?? '';
  return _globalDefaultModuleIds.contains(id) ? 'global' : 'local';
}

/// Locked (read-only child) when global and `child_may_edit` is not true.
bool projectModuleBindLocked(Map<String, dynamic> module) {
  if (module['enabled'] != true) return false;
  final may = module['child_may_edit'];
  if (may == true) return false;
  if (may == false) return true;
  return projectModuleBindKind(module) == 'global';
}

/// Project modules table — name, profile, Local/Global bind actions.
class ProjectModulesTable extends StatelessWidget {
  const ProjectModulesTable({
    super.key,
    required this.modules,
    required this.onOpen,
    required this.onBindChanged,
    this.showHeader = true,
  });

  final List<Map<String, dynamic>> modules;
  final ValueChanged<Map<String, dynamic>> onOpen;
  /// `bindKind` is `local` / `global` to enable, or `null` to unbind.
  final Future<void> Function(String moduleId, String? bindKind) onBindChanged;
  final bool showHeader;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final locale = Localizations.localeOf(context);
    final isRu = locale.languageCode == 'ru';
    final localLabel = isRu ? 'Локальная' : 'Local';
    final globalLabel = isRu ? 'Глобальная' : 'Global';

    final rows = modules.map((m) {
      final id = m['module_id'] as String;
      final hasProfiles = m['has_profiles'] == true;
      final profileName = m['profile_name'] as String?;
      final kind = projectModuleBindKind(m);
      final locked = projectModuleBindLocked(m);
      return AppEntityRow(
        id: id,
        title: m['name'] as String? ?? id,
        cells: {
          'profile': hasProfiles ? (profileName ?? l10n.commonEmDash) : l10n.commonEmDash,
        },
        cellWidgets: {
          'bind': _BindActions(
            moduleId: id,
            bindKind: kind,
            locked: locked,
            localLabel: localLabel,
            globalLabel: globalLabel,
            onBindChanged: onBindChanged,
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
          height: (rows.length * 56.0).clamp(120, 420),
          child: AppEntityCollection(
            mode: AppEntityCollectionMode.table,
            rows: rows,
            primaryColumnLabel: l10n.commonName,
            columns: [
              AppEntityColumn(id: 'profile', label: l10n.projectModuleProfileColumn),
              AppEntityColumn(
                id: 'bind',
                label: isRu ? 'Привязка' : 'Bind',
                width: 220,
                align: AppEntityColumnAlign.center,
              ),
            ],
            onOpen: (row) {
              final mod = modules.firstWhere((m) => m['module_id'] == row.id);
              onOpen(mod);
            },
            empty: EmptyPlaceholder(
              title: l10n.companyNoModules,
              icon: Icons.extension_outlined,
              fillViewport: false,
            ),
          ),
        ),
      ],
    );
  }
}

class _BindActions extends StatelessWidget {
  const _BindActions({
    required this.moduleId,
    required this.bindKind,
    required this.locked,
    required this.localLabel,
    required this.globalLabel,
    required this.onBindChanged,
  });

  final String moduleId;
  final String bindKind;
  final bool locked;
  final String localLabel;
  final String globalLabel;
  final Future<void> Function(String moduleId, String? bindKind) onBindChanged;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        if (locked)
          Padding(
            padding: const EdgeInsets.only(right: 4),
            child: Icon(
              Icons.lock_outline,
              size: 16,
              color: theme.colorScheme.onSurfaceVariant,
            ),
          ),
        _kindChip(
          context,
          label: localLabel,
          selected: bindKind == 'local',
          onTap: () => onBindChanged(
            moduleId,
            bindKind == 'local' ? null : 'local',
          ),
        ),
        const SizedBox(width: 4),
        _kindChip(
          context,
          label: globalLabel,
          selected: bindKind == 'global',
          onTap: () => onBindChanged(
            moduleId,
            bindKind == 'global' ? null : 'global',
          ),
        ),
      ],
    );
  }

  Widget _kindChip(
    BuildContext context, {
    required String label,
    required bool selected,
    required VoidCallback onTap,
  }) {
    return FilterChip(
      label: Text(label, style: const TextStyle(fontSize: 12)),
      selected: selected,
      visualDensity: VisualDensity.compact,
      materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
      onSelected: (_) => onTap(),
    );
  }
}
