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
import 'package:prodavan/l10n/app_localizations.dart';

/// Narrow-only hub: Employees / AI Keys / Containers / Cabinets / Modules.
class CompanyManagementPage extends StatelessWidget {
  const CompanyManagementPage({super.key, required this.companyId});

  final String companyId;

  Future<void> _open(BuildContext context, Widget page) {
    return Navigator.of(context).push<void>(
      MaterialPageRoute<void>(builder: (_) => page),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final items = <({IconData icon, String label, Widget page})>[
      (
        icon: Icons.group_outlined,
        label: l10n.navEmployees,
        page: CompanyEmployeesPage(companyId: companyId),
      ),
      (
        icon: Icons.key_outlined,
        label: l10n.navAiKeys,
        page: CompanyAiKeyListPage(companyId: companyId),
      ),
      (
        icon: Icons.dns_outlined,
        label: l10n.navContainers,
        page: CompanyProjectContainersPage(companyId: companyId),
      ),
      (
        icon: Icons.view_module_outlined,
        label: l10n.navCabinets,
        page: CompanyCabinetsPage(companyId: companyId),
      ),
      (
        icon: Icons.extension_outlined,
        label: l10n.navModules,
        page: CompanyModuleListPage(companyId: companyId),
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
            onTap: () => _open(context, item.page),
          );
        },
      ),
    );
  }
}
