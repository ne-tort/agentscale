import 'package:flutter/material.dart';

import 'package:prodavan/core/responsive/app_breakpoints.dart';
import 'package:prodavan/core/widgets/app_layout.dart';
import 'package:prodavan/core/widgets/app_shell_branch.dart';
import 'package:prodavan/features/admin/admin_cabinet_list_page.dart';
import 'package:prodavan/features/admin/admin_management_page.dart';
import 'package:prodavan/features/admin/admin_metrics_overview_page.dart';
import 'package:prodavan/features/admin/admin_module_list_page.dart';
import 'package:prodavan/features/admin/admin_project_containers_page.dart';
import 'package:prodavan/features/admin/admin_settings_body.dart';
import 'package:prodavan/features/admin/ai_key_list_page.dart';
import 'package:prodavan/features/admin/company_list_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Platform Admin shell — logo → overview; rail without Overview tab.
class AdminShell extends StatefulWidget {
  const AdminShell({super.key});

  @override
  State<AdminShell> createState() => _AdminShellState();
}

class _AdminShellState extends State<AdminShell> {
  static const _overviewIndex = 0;
  static const _sectionCount = 5;
  static const _settingsIndex = 6;

  int _contentIndex = _overviewIndex;
  int? _railSelected;
  int _narrowStackIndex = 1;
  bool _subpageOpen = false;

  void _onSubpageOpenChanged(bool open) {
    if (_subpageOpen != open) setState(() => _subpageOpen = open);
  }

  void _goOverview() {
    setState(() {
      _contentIndex = _overviewIndex;
      _railSelected = null;
      _narrowStackIndex = 0;
      _subpageOpen = false;
    });
  }

  void _selectRail(int index) {
    setState(() {
      _railSelected = index;
      _contentIndex = index + 1;
      _narrowStackIndex = 1;
      _subpageOpen = false;
    });
  }

  void _selectSettingsWide() {
    setState(() {
      _contentIndex = _settingsIndex;
      _railSelected = null;
      _subpageOpen = false;
    });
  }

  void _selectNarrowMain() {
    setState(() {
      _narrowStackIndex = 1;
      _subpageOpen = false;
    });
  }

  void _selectNarrowSettings() {
    setState(() {
      _narrowStackIndex = 2;
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
        selectedIndex: _narrowStackIndex == 1 ? 0 : null,
        trailingDestination: settingsDest,
        trailingSelected: _narrowStackIndex == 2,
        onTrailingSelected: _selectNarrowSettings,
        onDestinationSelected: (_) => _selectNarrowMain(),
        onLogoTap: _goOverview,
        destinations: [
          AppNavDestination(icon: Icons.apps_outlined, label: l10n.navManagement),
        ],
        body: IndexedStack(
          index: _narrowStackIndex,
          children: [
            AppShellBranch(
              active: _narrowStackIndex == 0,
              onSubpageOpenChanged: _narrowStackIndex == 0 ? _onSubpageOpenChanged : null,
              root: const AdminMetricsOverviewPage(embedded: true),
            ),
            AppShellBranch(
              active: _narrowStackIndex == 1,
              onSubpageOpenChanged: _narrowStackIndex == 1 ? _onSubpageOpenChanged : null,
              root: const AdminManagementPage(),
            ),
            const AdminSettingsBody(),
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
    ];

    return AppLayout(
      constrainBody: false,
      subpageOpen: _subpageOpen,
      selectedIndex: _railSelected,
      trailingDestination: settingsDest,
      trailingSelected: _contentIndex == _settingsIndex,
      onTrailingSelected: _selectSettingsWide,
      onDestinationSelected: _selectRail,
      onLogoTap: _goOverview,
      destinations: [
        AppNavDestination(icon: Icons.business_outlined, label: l10n.navCompanies),
        AppNavDestination(icon: Icons.key_outlined, label: l10n.navAiKeys),
        AppNavDestination(icon: Icons.dns_outlined, label: l10n.navContainers),
        AppNavDestination(icon: Icons.folder_outlined, label: l10n.navCabinets),
        AppNavDestination(icon: Icons.extension_outlined, label: l10n.navModules),
      ],
      body: IndexedStack(
        index: _contentIndex,
        children: [
          for (var i = 0; i < _sectionCount + 1; i++)
            AppShellBranch(
              active: _contentIndex == i,
              onSubpageOpenChanged: _contentIndex == i ? _onSubpageOpenChanged : null,
              root: mainPages[i],
            ),
          const AdminSettingsBody(),
        ],
      ),
    );
  }
}
