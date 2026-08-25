import 'package:flutter/material.dart';

import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/widgets/app_layout.dart';
import 'package:prodavan/features/company/company_cabinets_page.dart';
import 'package:prodavan/features/company/company_employees_page.dart';
import 'package:prodavan/features/company/company_overview_page.dart';
import 'package:prodavan/features/settings/open_app_settings.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Company admin shell — adaptive nav per ux-contract (L04).
class CompanyShell extends StatefulWidget {
  const CompanyShell({super.key});

  @override
  State<CompanyShell> createState() => _CompanyShellState();
}

class _CompanyShellState extends State<CompanyShell> {
  int _index = 0;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final companyId = companyContext.companyId!;
    final title = companyContext.companyName ?? companyId;
    final pages = [
      CompanyOverviewPage(companyId: companyId),
      CompanyEmployeesPage(companyId: companyId),
      CompanyCabinetsPage(companyId: companyId),
    ];

    return AppLayout(
      title: Text(title),
      selectedIndex: _index,
      onDestinationSelected: (i) => setState(() => _index = i),
      onOpenSettings: () => openAppSettings(context),
      onLogoTap: () => setState(() => _index = 0),
      destinations: [
        AppNavDestination(icon: Icons.dashboard_outlined, label: l10n.navOverview),
        AppNavDestination(icon: Icons.group_outlined, label: l10n.navEmployees),
        AppNavDestination(icon: Icons.view_module_outlined, label: l10n.navCabinets),
      ],
      body: pages[_index],
    );
  }
}
