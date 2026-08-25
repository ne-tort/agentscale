import 'package:flutter/material.dart';

import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/widgets/app_layout.dart';
import 'package:prodavan/features/company/company_cabinets_page.dart';
import 'package:prodavan/features/company/company_employees_page.dart';
import 'package:prodavan/features/company/company_overview_page.dart';

/// Company admin shell — adaptive nav per ux-contract (L04).
class CompanyShell extends StatefulWidget {
  const CompanyShell({super.key});

  @override
  State<CompanyShell> createState() => _CompanyShellState();
}

class _CompanyShellState extends State<CompanyShell> {
  int _index = 0;

  static const _destinations = [
    AppNavDestination(icon: Icons.dashboard_outlined, label: 'Overview'),
    AppNavDestination(icon: Icons.group_outlined, label: 'Employees'),
    AppNavDestination(icon: Icons.view_module_outlined, label: 'Cabinets'),
  ];

  @override
  Widget build(BuildContext context) {
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
      destinations: _destinations,
      body: pages[_index],
    );
  }
}
