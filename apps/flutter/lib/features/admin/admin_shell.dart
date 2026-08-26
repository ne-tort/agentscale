import 'package:flutter/material.dart';

import 'package:prodavan/core/widgets/app_layout.dart';
import 'package:prodavan/features/admin/admin_cabinets_stub_page.dart';
import 'package:prodavan/features/admin/admin_metrics_overview_page.dart';
import 'package:prodavan/features/admin/admin_project_containers_page.dart';
import 'package:prodavan/features/admin/ai_key_list_page.dart';
import 'package:prodavan/features/admin/company_list_page.dart';
import 'package:prodavan/features/settings/open_app_settings.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Platform Admin shell — Overview + Companies + AI Keys + Containers + Cabinets.
class AdminShell extends StatefulWidget {
  const AdminShell({super.key});

  @override
  State<AdminShell> createState() => _AdminShellState();
}

class _AdminShellState extends State<AdminShell> {
  int _index = 0;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final destinations = [
      AppNavDestination(icon: Icons.dashboard_outlined, label: l10n.navOverview),
      AppNavDestination(icon: Icons.business_outlined, label: l10n.navCompanies),
      AppNavDestination(icon: Icons.key_outlined, label: l10n.navAiKeys),
      AppNavDestination(icon: Icons.dns_outlined, label: l10n.navContainers),
      AppNavDestination(icon: Icons.folder_outlined, label: l10n.navCabinets),
    ];

    final pages = const [
      AdminMetricsOverviewPage(embedded: true),
      AdminCompanyListPage(embedded: true),
      AdminAiKeyListPage(embedded: true),
      AdminProjectContainersPage(embedded: true),
      AdminCabinetsStubPage(embedded: true),
    ];

    return AppLayout(
      constrainBody: false,
      selectedIndex: _index,
      onDestinationSelected: (i) => setState(() => _index = i),
      onOpenSettings: () => openAppSettings(context),
      onLogoTap: () => setState(() => _index = 0),
      destinations: destinations,
      body: IndexedStack(index: _index, children: pages),
    );
  }
}
