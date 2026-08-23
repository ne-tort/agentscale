import 'package:flutter/material.dart';

import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/features/company/company_cabinets_page.dart';
import 'package:prodavan/features/company/company_employees_page.dart';
import 'package:prodavan/features/company/company_overview_page.dart';

/// Company admin shell — bottom nav per ux-contract (L04).
class CompanyShell extends StatefulWidget {
  const CompanyShell({super.key});

  @override
  State<CompanyShell> createState() => _CompanyShellState();
}

class _CompanyShellState extends State<CompanyShell> {
  int _index = 0;

  @override
  Widget build(BuildContext context) {
    final companyId = companyContext.companyId!;
    final title = companyContext.companyName ?? companyId;
    final pages = [
      CompanyOverviewPage(companyId: companyId),
      CompanyEmployeesPage(companyId: companyId),
      CompanyCabinetsPage(companyId: companyId),
    ];

    return AppScaffold(
      title: Text(title),
      body: pages[_index],
      bottomNavigationBar: NavigationBar(
        selectedIndex: _index,
        onDestinationSelected: (i) => setState(() => _index = i),
        destinations: const [
          NavigationDestination(icon: Icon(Icons.dashboard_outlined), label: 'Overview'),
          NavigationDestination(icon: Icon(Icons.group_outlined), label: 'Employees'),
          NavigationDestination(icon: Icon(Icons.view_module_outlined), label: 'Cabinets'),
        ],
      ),
    );
  }
}
