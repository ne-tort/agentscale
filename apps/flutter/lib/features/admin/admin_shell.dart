import 'package:flutter/material.dart';

import 'package:prodavan/core/responsive/app_breakpoints.dart';
import 'package:prodavan/core/widgets/app_layout.dart';
import 'package:prodavan/core/widgets/app_shell_branch.dart';
import 'package:prodavan/features/admin/admin_cabinet_list_page.dart';
import 'package:prodavan/features/admin/admin_management_page.dart';
import 'package:prodavan/features/admin/admin_metrics_overview_page.dart';
import 'package:prodavan/features/admin/admin_module_list_page.dart';
import 'package:prodavan/features/admin/admin_project_containers_page.dart';
import 'package:prodavan/features/admin/ai_key_list_page.dart';
import 'package:prodavan/features/admin/company_list_page.dart';
import 'package:prodavan/features/settings/settings_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Platform Admin shell — Overview + management sections (+ mobile Management hub).
class AdminShell extends StatefulWidget {
  const AdminShell({super.key});

  @override
  State<AdminShell> createState() => _AdminShellState();
}

class _AdminShellState extends State<AdminShell> {
  static const _mainCount = 6;
  static const _settingsIndex = 6;

  int _contentIndex = 0;
  int _mainHighlight = 0;
  int _narrowIndex = 0;
  bool _subpageOpen = false;

  void _onSubpageOpenChanged(bool open) {
    if (_subpageOpen != open) setState(() => _subpageOpen = open);
  }

  void _selectMainWide(int index) {
    setState(() {
      _mainHighlight = index;
      _contentIndex = index;
      _subpageOpen = false;
    });
  }

  void _selectSettingsWide() {
    setState(() {
      _contentIndex = _settingsIndex;
      _subpageOpen = false;
    });
  }

  void _selectNarrow(int index) {
    setState(() {
      _narrowIndex = index;
      _subpageOpen = false;
    });
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final narrow = AppBreakpoints.isNarrow(context);
    final settingsDest = AppNavDestination(
      icon: Icons.settings_outlined,
      label: l10n.settings,
    );

    if (narrow) {
      return AppLayout(
        constrainBody: false,
        subpageOpen: _subpageOpen,
        selectedIndex: _narrowIndex.clamp(0, 1),
        trailingDestination: settingsDest,
        trailingSelected: _narrowIndex == 2,
        onTrailingSelected: () => _selectNarrow(2),
        onDestinationSelected: _selectNarrow,
        onLogoTap: () => _selectNarrow(0),
        destinations: [
          AppNavDestination(icon: Icons.dashboard_outlined, label: l10n.navOverview),
          AppNavDestination(icon: Icons.apps_outlined, label: l10n.navManagement),
        ],
        body: IndexedStack(
          index: _narrowIndex,
          children: [
            AppShellBranch(
              active: _narrowIndex == 0,
              onSubpageOpenChanged: _narrowIndex == 0 ? _onSubpageOpenChanged : null,
              root: const AdminMetricsOverviewPage(embedded: true),
            ),
            AppShellBranch(
              active: _narrowIndex == 1,
              onSubpageOpenChanged: _narrowIndex == 1 ? _onSubpageOpenChanged : null,
              root: const AdminManagementPage(),
            ),
            const SettingsPage(embedded: true),
          ],
        ),
      );
    }

    const mainPages = [
      AdminMetricsOverviewPage(embedded: true),
      AdminCompanyListPage(embedded: true),
      AdminAiKeyListPage(embedded: true),
      AdminProjectContainersPage(embedded: true),
      AdminCabinetListPage(embedded: true),
      AdminModuleListPage(embedded: true),
      SettingsPage(embedded: true),
    ];

    return AppLayout(
      constrainBody: false,
      subpageOpen: _subpageOpen,
      selectedIndex: _mainHighlight,
      trailingDestination: settingsDest,
      trailingSelected: _contentIndex == _settingsIndex,
      onTrailingSelected: _selectSettingsWide,
      onDestinationSelected: _selectMainWide,
      onLogoTap: () => _selectMainWide(0),
      destinations: [
        AppNavDestination(icon: Icons.dashboard_outlined, label: l10n.navOverview),
        AppNavDestination(icon: Icons.business_outlined, label: l10n.navCompanies),
        AppNavDestination(icon: Icons.key_outlined, label: l10n.navAiKeys),
        AppNavDestination(icon: Icons.dns_outlined, label: l10n.navContainers),
        AppNavDestination(icon: Icons.folder_outlined, label: l10n.navCabinets),
        AppNavDestination(icon: Icons.extension_outlined, label: l10n.navModules),
      ],
      body: IndexedStack(
        index: _contentIndex,
        children: [
          for (var i = 0; i < _mainCount; i++)
            AppShellBranch(
              active: _contentIndex == i,
              onSubpageOpenChanged: _contentIndex == i ? _onSubpageOpenChanged : null,
              root: mainPages[i],
            ),
          const SettingsPage(embedded: true),
        ],
      ),
    );
  }
}
