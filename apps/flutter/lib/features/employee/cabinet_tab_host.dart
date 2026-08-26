import 'package:flutter/material.dart';

import 'package:prodavan/core/widgets/app_collection_view_mode.dart';
import 'package:prodavan/features/employee/cabinet_context_tab_page.dart';
import 'package:prodavan/features/employee/cabinet_placeholder_tab_page.dart';
import 'package:prodavan/features/employee/cabinet_tables_tab_page.dart';
import 'package:prodavan/features/employee/cabinet_tools_tab_page.dart';
import 'package:prodavan/features/employee/project_list_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Routes cabinet meta tabs to L05/L06 interpreters by view_slug.
class CabinetTabHost extends StatelessWidget {
  const CabinetTabHost({
    super.key,
    required this.cabinetId,
    required this.cabinetName,
    required this.tab,
    required this.projectsViewModeStore,
  });

  final String cabinetId;
  final Map<String, dynamic> tab;
  final String cabinetName;
  final AppCollectionViewModeStore projectsViewModeStore;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final slug = tab['view_slug'] as String? ?? '';
    final title = tab['title'] as String? ?? 'Tab';

    switch (slug) {
      case 'projects':
        return ProjectListPage(
          cabinetId: cabinetId,
          viewModeStore: projectsViewModeStore,
        );
      case 'tables':
        return CabinetTablesTabPage(cabinetId: cabinetId);
      case 'tools':
        return CabinetToolsTabPage(cabinetId: cabinetId);
      case 'context':
        return CabinetContextTabPage(cabinetId: cabinetId, cabinetName: cabinetName);
      case 'chat':
        return ProjectListPage(
          cabinetId: cabinetId,
          viewModeStore: projectsViewModeStore,
        );
      default:
        final tableSlug = tab['table_slug'] as String?;
        if (tableSlug != null && tableSlug.isNotEmpty) {
          return CabinetTablesTabPage(
            cabinetId: cabinetId,
            initialTableSlug: tableSlug,
          );
        }
        if (slug.isNotEmpty) {
          return CabinetTablesTabPage(
            cabinetId: cabinetId,
            initialTableSlug: slug,
          );
        }
        return CabinetPlaceholderTabPage(
          title: title,
          hint: slug.isEmpty
              ? l10n.cabinetUnknownTabNoViewSlug
              : l10n.cabinetNoInterpreterForView(slug),
        );
    }
  }
}
