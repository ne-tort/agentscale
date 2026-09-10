import 'package:flutter/material.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/preferences/app_nav_preference.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/features/meta/meta_icon.dart';
import 'package:prodavan/features/meta/meta_label.dart';
import 'package:prodavan/features/meta/runtime/module_runtime_scope.dart';
import 'package:prodavan/features/meta/widgets/remote_database_picker_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Nav tile «База» → live remote SQL database selector (single switch).
class RemoteDatabasePickerField extends StatelessWidget {
  const RemoteDatabasePickerField({
    super.key,
    required this.label,
    required this.databaseName,
    required this.readOnly,
    required this.emptyStyleWarning,
    required this.emptyLabel,
    required this.listActionId,
    required this.rowId,
    required this.onSelected,
    this.onClosed,
    this.icon = Icons.storage_outlined,
  });

  final String label;
  final String? databaseName;
  final bool readOnly;
  final bool emptyStyleWarning;
  final String emptyLabel;
  final String listActionId;
  final String rowId;
  final Future<void> Function(String databaseName) onSelected;
  final Future<void> Function()? onClosed;
  final IconData icon;

  @override
  Widget build(BuildContext context) {
    final selected = databaseName != null && databaseName!.trim().isNotEmpty;
    final warning = context.appColors.warning;
    return AppNavPreference(
      title: label,
      icon: icon,
      subtitle: Text(
        selected ? databaseName!.trim() : emptyLabel,
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
            builder: (_) => RemoteDatabasePickerPage(
              api: scope.api,
              cabinetId: scope.cabinetId,
              moduleId: scope.moduleId,
              listActionId: listActionId,
              rowId: rowId,
              selectedDatabase: databaseName,
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

String remoteDatabaseEmptyLabel(
  Map<String, dynamic>? fieldCfg,
  AppLocalizations l10n,
  Locale locale,
) {
  final raw = fieldCfg?['empty_label'];
  final resolved = resolveMetaLabel(raw, l10n, locale: locale);
  if (resolved.isNotEmpty) return resolved;
  return locale.languageCode == 'en' ? 'Not selected' : 'Не выбрана';
}

IconData remoteDatabaseIcon(String? name) =>
    metaIconFromName(name, fallback: Icons.storage_outlined);

bool remotePickerIsAuthFailure(Object error) {
  if (error is! ProdavanApiException) return false;
  return error.body.contains('REMOTE_AUTH_FAILED');
}
