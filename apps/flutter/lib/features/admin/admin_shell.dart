import 'package:flutter/material.dart';

import 'package:prodavan/core/responsive/app_breakpoints.dart';
import 'package:prodavan/core/widgets/app_layout.dart';
import 'package:prodavan/features/admin/admin_cabinet_list_page.dart';
import 'package:prodavan/features/admin/admin_management_page.dart';
import 'package:prodavan/features/admin/admin_metrics_overview_page.dart';
import 'package:prodavan/features/admin/admin_project_containers_page.dart';
import 'package:prodavan/features/admin/ai_key_list_page.dart';
import 'package:prodavan/features/admin/company_list_page.dart';
import 'package:prodavan/features/settings/open_app_settings.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Platform Admin shell — Overview + management sections (+ mobile Management hub).
class AdminShell extends StatefulWidget {
  const AdminShell({super.key});

  @override
  State<AdminShell> createState() => _AdminShellState();
}

class _AdminShellState extends State<AdminShell> {
  /// Wide/medium rail: 0 Overview … 4 Cabinets.
  int _railIndex = 0;

  /// Narrow bottom: 0 Overview, 1 Management hub.
  int _narrowIndex = 0;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final narrow = AppBreakpoints.isNarrow(context);

    if (narrow) {
      return AppLayout(
        constrainBody: false,
        selectedIndex: _narrowIndex,
        onDestinationSelected: (i) => setState(() => _narrowIndex = i),
        onOpenSettings: () => openAppSettings(context),
        onLogoTap: () => setState(() => _narrowIndex = 0),
        destinations: [
          AppNavDestination(icon: Icons.dashboard_outlined, label: l10n.navOverview),
          AppNavDestination(icon: Icons.apps_outlined, label: l10n.navManagement),
        ],
        body: _narrowIndex == 0
            ? const AdminMetricsOverviewPage(embedded: true)
            : const AdminManagementPage(),
      );
    }

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
      AdminCabinetListPage(embedded: true),
    ];

    return AppLayout(
      constrainBody: false,
      selectedIndex: _railIndex,
      onDestinationSelected: (i) => setState(() => _railIndex = i),
      onOpenSettings: () => openAppSettings(context),
      onLogoTap: () => setState(() => _railIndex = 0),
      destinations: destinations,
      body: IndexedStack(index: _railIndex, children: pages),
    );
  }
}
