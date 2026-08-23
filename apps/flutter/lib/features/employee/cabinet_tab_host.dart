import 'package:flutter/material.dart';

import 'package:prodavan/features/employee/cabinet_placeholder_tab_page.dart';
import 'package:prodavan/features/employee/cabinet_tables_tab_page.dart';
import 'package:prodavan/features/employee/cabinet_tools_tab_page.dart';
import 'package:prodavan/features/employee/project_list_page.dart';

/// Routes cabinet meta tabs to L05/L06 interpreters by view_slug.
class CabinetTabHost extends StatelessWidget {
  const CabinetTabHost({
    super.key,
    required this.cabinetId,
    required this.tab,
  });

  final String cabinetId;
  final Map<String, dynamic> tab;

  @override
  Widget build(BuildContext context) {
    final slug = tab['view_slug'] as String? ?? '';
    final title = tab['title'] as String? ?? 'Tab';

    switch (slug) {
      case 'projects':
        return ProjectListPage(cabinetId: cabinetId);
      case 'tables':
        return CabinetTablesTabPage(cabinetId: cabinetId);
      case 'tools':
        return CabinetToolsTabPage(cabinetId: cabinetId);
      case 'chat':
        return CabinetPlaceholderTabPage(
          title: title,
          hint: 'Open a project from the Projects tab to chat with the agent.',
        );
      case 'context':
        return CabinetPlaceholderTabPage(
          title: title,
          hint: 'Cabinet context and memory views will appear here (L06 bundle).',
        );
      default:
        return CabinetPlaceholderTabPage(
          title: title,
          hint: slug.isEmpty
              ? 'Unknown tab — no view_slug from meta.'
              : 'No interpreter registered for view "$slug".',
        );
    }
  }
}
