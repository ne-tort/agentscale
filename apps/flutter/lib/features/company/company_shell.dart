import 'package:flutter/material.dart';

import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/widgets/app_layout.dart';
import 'package:prodavan/core/widgets/app_shell_branch.dart';
import 'package:prodavan/features/company/company_cabinets_page.dart';
import 'package:prodavan/features/company/company_employees_page.dart';
import 'package:prodavan/features/company/company_overview_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Company admin shell — adaptive nav per ux-contract (L04).
/// Page titles/actions live on each tab's [AppScaffold] (same pattern as AdminShell).
class CompanyShell extends StatefulWidget {
  const CompanyShell({super.key});

  @override
  State<CompanyShell> createState() => _CompanyShellState();
}

class _CompanyShellState extends State<CompanyShell> {
  int _index = 0;
  bool _subpageOpen = false;

  void _onSubpageOpenChanged(bool open) {
    if (_subpageOpen != open) setState(() => _subpageOpen = open);
  }

  void _selectTab(int index) {
    setState(() {
      _index = index;
      _subpageOpen = false;
    });
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final companyId = companyContext.companyId!;
    final pages = [
      CompanyOverviewPage(companyId: companyId),
      CompanyEmployeesPage(companyId: companyId),
      CompanyCabinetsPage(companyId: companyId),
    ];

    return AppLayout(
      constrainBody: false,
      subpageOpen: _subpageOpen,
      selectedIndex: _index,
      onDestinationSelected: _selectTab,
      onLogoTap: () => _selectTab(0),
      destinations: [
        AppNavDestination(icon: Icons.dashboard_outlined, label: l10n.navOverview),
        AppNavDestination(icon: Icons.group_outlined, label: l10n.navEmployees),
        AppNavDestination(icon: Icons.view_module_outlined, label: l10n.navCabinets),
      ],
      body: IndexedStack(
        index: _index,
        children: [
          for (var i = 0; i < pages.length; i++)
            AppShellBranch(
              active: _index == i,
              onSubpageOpenChanged: _index == i ? _onSubpageOpenChanged : null,
              root: pages[i],
            ),
        ],
      ),
    );
  }
}
