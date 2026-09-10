import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/app_nav_preference.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/features/meta/meta_icon.dart';
import 'package:prodavan/features/meta/meta_label.dart';
import 'package:prodavan/features/meta/runtime/module_runtime_scope.dart';
import 'package:prodavan/features/meta/widgets/remote_table_picker_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Nav tile «Таблица» → live remote SQL table selector (single switch).
class RemoteTablePickerField extends StatelessWidget {
  const RemoteTablePickerField({
    super.key,
    required this.label,
    required this.tableName,
    required this.readOnly,
    required this.emptyStyleWarning,
    required this.emptyLabel,
    required this.listActionId,
    required this.rowId,
    required this.onSelected,
    this.onClosed,
    this.icon = Icons.table_chart_outlined,
  });

  final String label;
  final String? tableName;
  final bool readOnly;
  final bool emptyStyleWarning;
  final String emptyLabel;
  final String listActionId;
  final String rowId;
  final Future<void> Function(String tableName) onSelected;
  final Future<void> Function()? onClosed;
  final IconData icon;

  @override
  Widget build(BuildContext context) {
    final selected = tableName != null && tableName!.trim().isNotEmpty;
    final warning = context.appColors.warning;
    return AppNavPreference(
      title: label,
      icon: icon,
      subtitle: Text(
        selected ? tableName!.trim() : emptyLabel,
        style: TextStyle(
          color: !selected && emptyStyleWarning ? warning : null,
        ),
      ),
      accentColor: !selected && emptyStyleWarning ? warning : null,
      enabled: !readOnly,
      onTap: () async {
        final scope = ModuleRuntimeScope.maybeOf(context);
        if (scope == null) return;
        final picked = await Navigator.of(context).push<String>(
          MaterialPageRoute(
            builder: (_) => RemoteTablePickerPage(
              api: scope.api,
              cabinetId: scope.cabinetId,
              moduleId: scope.moduleId,
              listActionId: listActionId,
              rowId: rowId,
              selectedTable: tableName,
            ),
          ),
        );
        if (picked == null || picked.isEmpty) {
          final closed = onClosed;
          if (closed != null) await closed();
          return;
        }
        await onSelected(picked);
      },
    );
  }
}

String remoteTableEmptyLabel(
  Map<String, dynamic>? fieldCfg,
  AppLocalizations l10n,
  Locale locale,
) {
  final raw = fieldCfg?['empty_label'];
  final resolved = resolveMetaLabel(raw, l10n, locale: locale);
  if (resolved.isNotEmpty) return resolved;
  return locale.languageCode == 'en' ? 'Not selected' : 'Не выбрана';
}

IconData remoteTableIcon(String? name) =>
    metaIconFromName(name, fallback: Icons.table_chart_outlined);
