import 'package:flutter/material.dart';

import 'package:prodavan/core/responsive/app_breakpoints.dart';
import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/widgets/app_layout.dart';
import 'package:prodavan/core/widgets/app_shell_branch.dart';
import 'package:prodavan/features/company/company_ai_key_list_page.dart';
import 'package:prodavan/features/company/company_cabinets_page.dart';
import 'package:prodavan/features/company/company_employees_page.dart';
import 'package:prodavan/features/company/company_management_page.dart';
import 'package:prodavan/features/company/company_module_list_page.dart';
import 'package:prodavan/features/company/company_overview_page.dart';
import 'package:prodavan/features/company/company_settings_body.dart';
import 'package:prodavan/features/company/company_project_containers_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Company admin shell — Admin-parity IA (P-CO-01).
class CompanyShell extends StatefulWidget {
  const CompanyShell({super.key});

  @override
  State<CompanyShell> createState() => _CompanyShellState();
}

class _CompanyShellState extends State<CompanyShell> {
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
    final companyId = companyContext.companyId!;
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
              root: CompanyOverviewPage(companyId: companyId),
            ),
            AppShellBranch(
              active: _narrowIndex == 1,
              onSubpageOpenChanged: _narrowIndex == 1 ? _onSubpageOpenChanged : null,
              root: CompanyManagementPage(companyId: companyId),
            ),
            CompanySettingsBody(companyId: companyId),
          ],
        ),
      );
    }

    final mainPages = [
      CompanyOverviewPage(companyId: companyId),
      CompanyEmployeesPage(companyId: companyId),
      CompanyAiKeyListPage(companyId: companyId, embedded: true),
      CompanyProjectContainersPage(companyId: companyId, embedded: true),
      CompanyCabinetsPage(companyId: companyId),
      CompanyModuleListPage(companyId: companyId, embedded: true),
      CompanySettingsBody(companyId: companyId),
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
        AppNavDestination(icon: Icons.group_outlined, label: l10n.navEmployees),
        AppNavDestination(icon: Icons.key_outlined, label: l10n.navAiKeys),
        AppNavDestination(icon: Icons.dns_outlined, label: l10n.navContainers),
        AppNavDestination(icon: Icons.view_module_outlined, label: l10n.navCabinets),
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
          CompanySettingsBody(companyId: companyId),
        ],
      ),
    );
  }
}
