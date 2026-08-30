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

/// Narrow-only hub: Companies / AI Keys / Projects / Cabinets / Modules + catalog modules.
class AdminManagementPage extends StatelessWidget {
  const AdminManagementPage({
    super.key,
    this.moduleEntries = const [],
  });

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
          body: ModuleShellNavPage(entry: entry, embedded: true),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final items = <({IconData icon, String label, VoidCallback onTap})>[
      (
        icon: Icons.business_outlined,
        label: l10n.navCompanies,
        onTap: () => _open(context, const AdminCompanyListPage()),
      ),
      (
        icon: Icons.key_outlined,
        label: l10n.navAiKeys,
        onTap: () => _open(context, const AdminAiKeyListPage()),
      ),
      (
        icon: Icons.dns_outlined,
        label: l10n.navContainers,
        onTap: () => _open(context, const AdminProjectContainersPage()),
      ),
      (
        icon: Icons.folder_outlined,
        label: l10n.navCabinets,
        onTap: () => _open(context, const AdminCabinetListPage()),
      ),
      (
        icon: Icons.extension_outlined,
        label: l10n.navModules,
        onTap: () => _open(context, const AdminModuleListPage()),
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
