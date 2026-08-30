import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_list_item.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_trailing_chevron.dart';
import 'package:prodavan/features/company/company_ai_key_list_page.dart';
import 'package:prodavan/features/company/company_cabinets_page.dart';
import 'package:prodavan/features/company/company_employees_page.dart';
import 'package:prodavan/features/company/company_module_list_page.dart';
import 'package:prodavan/features/company/company_project_containers_page.dart';
import 'package:prodavan/features/meta/meta_icon.dart';
import 'package:prodavan/features/meta/module_shell_nav_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Narrow-only hub: Employees / AI Keys / Containers / Cabinets / Modules + catalog modules.
class CompanyManagementPage extends StatelessWidget {
  const CompanyManagementPage({
    super.key,
    required this.companyId,
    this.moduleEntries = const [],
  });

  final String companyId;
  final List<ShellNavEntry> moduleEntries;

  Future<void> _open(BuildContext context, Widget page) {
    return Navigator.of(context).push<void>(
      MaterialPageRoute<void>(builder: (_) => page),
    );
  }

  Future<void> _openModule(BuildContext context, ShellNavEntry entry) {
    return Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => AppScaffold(
          title: Text(entry.label),
          body: ModuleShellNavPage(
            entry: entry,
            embedded: true,
            companyId: companyId,
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final items = <({IconData icon, String label, VoidCallback onTap})>[
      (
        icon: Icons.group_outlined,
        label: l10n.navEmployees,
        onTap: () => _open(context, CompanyEmployeesPage(companyId: companyId)),
      ),
      (
        icon: Icons.key_outlined,
        label: l10n.navAiKeys,
        onTap: () => _open(context, CompanyAiKeyListPage(companyId: companyId)),
      ),
      (
        icon: Icons.dns_outlined,
        label: l10n.navContainers,
        onTap: () => _open(context, CompanyProjectContainersPage(companyId: companyId)),
      ),
      (
        icon: Icons.view_module_outlined,
        label: l10n.navCabinets,
        onTap: () => _open(context, CompanyCabinetsPage(companyId: companyId)),
      ),
      (
        icon: Icons.extension_outlined,
        label: l10n.navModules,
        onTap: () => _open(context, CompanyModuleListPage(companyId: companyId)),
      ),
      for (final entry in moduleEntries)
        (
          icon: entry.icon,
          label: entry.label,
          onTap: () => _openModule(context, entry),
        ),
    ];

    return AppScaffold(
      title: Text(l10n.navManagement),
      body: ListView.separated(
        padding: const EdgeInsets.all(AppSpacing.md),
        itemCount: items.length,
        separatorBuilder: (_, _) => const SizedBox(height: AppSpacing.sm),
        itemBuilder: (context, i) {
          final item = items[i];
          return AppListItem(
            leading: Icon(item.icon),
            title: Text(item.label),
            trailing: const AppTrailingChevron(),
            onTap: item.onTap,
          );
        },
      ),
    );
  }
}
