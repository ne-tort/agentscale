import 'package:flutter/material.dart';

import 'package:prodavan/features/admin/admin_metrics_overview_page.dart';
import 'package:prodavan/features/admin/ai_key_list_page.dart';
import 'package:prodavan/features/admin/company_list_page.dart';

/// Platform Admin shell — Overview + Companies + AI Keys (L04).
class AdminShell extends StatefulWidget {
  const AdminShell({super.key});

  @override
  State<AdminShell> createState() => _AdminShellState();
}

class _AdminShellState extends State<AdminShell> {
  int _index = 0;

  @override
  Widget build(BuildContext context) {
    final pages = const [
      AdminMetricsOverviewPage(embedded: true),
      AdminCompanyListPage(embedded: true),
      AdminAiKeyListPage(embedded: true),
    ];

    return Scaffold(
      body: IndexedStack(index: _index, children: pages),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _index,
        onDestinationSelected: (i) => setState(() => _index = i),
        destinations: const [
          NavigationDestination(icon: Icon(Icons.dashboard_outlined), label: 'Overview'),
          NavigationDestination(icon: Icon(Icons.business_outlined), label: 'Companies'),
          NavigationDestination(icon: Icon(Icons.key_outlined), label: 'AI Keys'),
        ],
      ),
    );
  }
}
