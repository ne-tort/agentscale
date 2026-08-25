import 'package:flutter/material.dart';

import 'package:prodavan/core/widgets/app_layout.dart';
import 'package:prodavan/features/admin/admin_metrics_overview_page.dart';
import 'package:prodavan/features/admin/admin_starter_bundles_page.dart';
import 'package:prodavan/features/admin/ai_key_list_page.dart';
import 'package:prodavan/features/admin/company_list_page.dart';

/// Platform Admin shell — Overview + Companies + AI Keys + Bundles (L04).
class AdminShell extends StatefulWidget {
  const AdminShell({super.key});

  @override
  State<AdminShell> createState() => _AdminShellState();
}

class _AdminShellState extends State<AdminShell> {
  int _index = 0;

  static const _destinations = [
    AppNavDestination(icon: Icons.dashboard_outlined, label: 'Overview'),
    AppNavDestination(icon: Icons.business_outlined, label: 'Companies'),
    AppNavDestination(icon: Icons.key_outlined, label: 'AI Keys'),
    AppNavDestination(icon: Icons.inventory_2_outlined, label: 'Bundles'),
  ];

  @override
  Widget build(BuildContext context) {
    final pages = const [
      AdminMetricsOverviewPage(embedded: true),
      AdminCompanyListPage(embedded: true),
      AdminAiKeyListPage(embedded: true),
      AdminStarterBundlesPage(embedded: true),
    ];

    return AppLayout(
      // Nested pages own AppScaffold (AppBar + content max-width).
      constrainBody: false,
      selectedIndex: _index,
      onDestinationSelected: (i) => setState(() => _index = i),
      destinations: _destinations,
      body: IndexedStack(index: _index, children: pages),
    );
  }
}
