import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_list_item.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_trailing_chevron.dart';
import 'package:prodavan/features/admin/admin_cabinet_list_page.dart';
import 'package:prodavan/features/admin/admin_module_list_page.dart';
import 'package:prodavan/features/admin/admin_project_containers_page.dart';
import 'package:prodavan/features/admin/ai_key_list_page.dart';
import 'package:prodavan/features/admin/company_list_page.dart';
import 'package:prodavan/features/meta/meta_icon.dart';
import 'package:prodavan/features/meta/module_shell_nav_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Narrow-only hub: Companies / AI Keys / Projects / Cabinets / Modules + module shell nav.
class AdminManagementPage extends StatelessWidget {
  const AdminManagementPage({super.key, this.extraShellNav = const []});

  final List<ShellNavEntry> extraShellNav;

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
        icon: Icons.business_outlined,
        label: l10n.navCompanies,
        page: const AdminCompanyListPage(),
      ),
      (
        icon: Icons.key_outlined,
        label: l10n.navAiKeys,
        page: const AdminAiKeyListPage(),
      ),
      (
        icon: Icons.dns_outlined,
        label: l10n.navContainers,
        page: const AdminProjectContainersPage(),
      ),
      (
        icon: Icons.folder_outlined,
        label: l10n.navCabinets,
        page: const AdminCabinetListPage(),
      ),
      (
        icon: Icons.extension_outlined,
        label: l10n.navModules,
        page: const AdminModuleListPage(),
      ),
      for (final entry in extraShellNav)
        (
          icon: entry.icon,
          label: entry.label,
          page: ModuleShellNavPage(entry: entry),
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
