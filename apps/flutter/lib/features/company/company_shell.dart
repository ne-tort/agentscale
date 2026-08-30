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
import 'package:prodavan/features/meta/meta_icon.dart';
import 'package:prodavan/features/meta/module_shell_nav_page.dart';
import 'package:prodavan/features/meta/shell_nav_loader.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Company admin shell — logo → overview; rail without Overview tab.
class CompanyShell extends StatefulWidget {
  const CompanyShell({super.key});

  @override
  State<CompanyShell> createState() => _CompanyShellState();
}

class _CompanyShellState extends State<CompanyShell> {
  static const _overviewIndex = 0;
  static const _platformCount = 5;

  int _contentIndex = _overviewIndex;
  int? _railSelected;
  int _narrowStackIndex = 1;
  bool _subpageOpen = false;
  bool _navLoading = true;
  List<ShellNavEntry> _railEntries = const [];
  List<ShellNavEntry> _managementEntries = const [];

  int get _settingsIndex => 1 + _platformCount + _railEntries.length;

  @override
  void initState() {
    super.initState();
    _loadNav();
  }

  Future<void> _loadNav() async {
    final companyId = companyContext.companyId;
    if (companyId == null) return;
    setState(() => _navLoading = true);
    try {
      final bundle = await ShellNavLoader.loadCompanyBundle(companyContext.api, companyId);
      if (!mounted) return;
      setState(() {
        _railEntries = bundle.rail;
        _managementEntries = bundle.management;
        _navLoading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _railEntries = const [];
        _managementEntries = const [];
        _navLoading = false;
      });
    }
  }

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

  void _selectNarrowSettings() {
    setState(() {
      _narrowStackIndex = 2;
      _subpageOpen = false;
    });
  }

  void _onNarrowDestinationSelected(int index) {
    setState(() {
      _narrowStackIndex = index == 0 ? 0 : 1;
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
        selectedIndex: _narrowStackIndex == 2 ? null : _narrowStackIndex.clamp(0, 1),
        trailingDestination: settingsDest,
        trailingSelected: _narrowStackIndex == 2,
        onTrailingSelected: _selectNarrowSettings,
        onDestinationSelected: _onNarrowDestinationSelected,
        onLogoTap: _goOverview,
        destinations: [
          AppNavDestination(icon: Icons.dashboard_outlined, label: l10n.navOverview),
          AppNavDestination(icon: Icons.apps_outlined, label: l10n.navManagement),
        ],
        body: IndexedStack(
          index: _narrowStackIndex,
          children: [
            AppShellBranch(
              active: _narrowStackIndex == 0,
              onSubpageOpenChanged: _narrowStackIndex == 0 ? _onSubpageOpenChanged : null,
              root: CompanyOverviewPage(companyId: companyId),
            ),
            AppShellBranch(
              active: _narrowStackIndex == 1,
              onSubpageOpenChanged: _narrowStackIndex == 1 ? _onSubpageOpenChanged : null,
              root: CompanyManagementPage(
                companyId: companyId,
                moduleEntries: _managementEntries,
              ),
            ),
            CompanySettingsBody(companyId: companyId),
          ],
        ),
      );
    }

    final platformDestinations = [
      AppNavDestination(icon: Icons.group_outlined, label: l10n.navEmployees),
      AppNavDestination(icon: Icons.key_outlined, label: l10n.navAiKeys),
      AppNavDestination(icon: Icons.dns_outlined, label: l10n.navContainers),
      AppNavDestination(icon: Icons.view_module_outlined, label: l10n.navCabinets),
      AppNavDestination(icon: Icons.extension_outlined, label: l10n.navModules),
    ];

    final railDestinations = [
      ...platformDestinations,
      ..._railEntries.map((e) => AppNavDestination(icon: e.icon, label: e.label)),
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
      destinations: railDestinations,
      body: _navLoading
          ? const Center(child: CircularProgressIndicator())
          : IndexedStack(
              index: _contentIndex,
              children: _buildWideBranches(companyId),
            ),
    );
  }

  List<Widget> _buildWideBranches(String companyId) {
    final platformPages = [
      CompanyOverviewPage(companyId: companyId),
      CompanyEmployeesPage(companyId: companyId),
      CompanyAiKeyListPage(companyId: companyId, embedded: true),
      CompanyProjectContainersPage(companyId: companyId, embedded: true),
      CompanyCabinetsPage(companyId: companyId),
      CompanyModuleListPage(companyId: companyId, embedded: true),
    ];

    final modulePages = _railEntries
        .map(
          (e) => ModuleShellNavPage(
            entry: e,
            embedded: true,
            companyId: companyId,
          ),
        )
        .toList();

    final mainPages = [
      ...platformPages,
      ...modulePages,
    ];

    return [
      for (var i = 0; i < mainPages.length; i++)
        AppShellBranch(
          active: _contentIndex == i,
          onSubpageOpenChanged: _contentIndex == i ? _onSubpageOpenChanged : null,
          root: mainPages[i],
        ),
      CompanySettingsBody(companyId: companyId),
    ];
  }
}
