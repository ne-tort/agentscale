import 'package:flutter/material.dart';

import 'package:prodavan/core/refresh/app_auto_refresh.dart';
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
import 'package:prodavan/features/company/company_project_containers_page.dart';
import 'package:prodavan/features/meta/meta_icon.dart';
import 'package:prodavan/features/meta/module_shell_nav_page.dart';
import 'package:prodavan/features/meta/shell_nav_loader.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Company admin shell — Admin-parity IA (P-CO-01).
class CompanyShell extends StatefulWidget {
  const CompanyShell({super.key, this.shellNavEntries});

  /// Optional injected nav entries (tests). When null, loaded from API.
  final List<ShellNavEntry>? shellNavEntries;

  @override
  State<CompanyShell> createState() => _CompanyShellState();
}

class _CompanyShellState extends State<CompanyShell> {
  static const _fixedRailCount = 6;

  int _railIndex = 0;
  int _narrowIndex = 0;
  bool _subpageOpen = false;
  List<ShellNavEntry> _shellNav = const [];
  late final AppAutoRefreshBinder _autoRefresh;

  @override
  void initState() {
    super.initState();
    _shellNav = widget.shellNavEntries ?? const [];
    _autoRefresh = AppAutoRefreshBinder(
      onTick: () => _reloadShellNav(silent: true),
      isActive: () => widget.shellNavEntries == null && appAutoRefreshIsActive(context),
    )..attach();
    if (widget.shellNavEntries == null) {
      _reloadShellNav();
    }
  }

  @override
  void dispose() {
    _autoRefresh.dispose();
    super.dispose();
  }

  Future<void> _reloadShellNav({bool silent = false}) async {
    if (widget.shellNavEntries != null) return;
    final companyId = companyContext.companyId;
    if (companyId == null) return;
    try {
      final entries = await ShellNavLoader.loadCompany(companyContext.api, companyId);
      if (!mounted) return;
      final maxIndex = _fixedRailCount + entries.length - 1;
      setState(() {
        _shellNav = entries;
        if (_railIndex > maxIndex) _railIndex = 0;
      });
    } catch (_) {
      if (!silent && mounted) setState(() => _shellNav = const []);
    }
  }

  void _onSubpageOpenChanged(bool open) {
    if (_subpageOpen != open) setState(() => _subpageOpen = open);
  }

  void _selectRail(int index) {
    setState(() {
      _railIndex = index;
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

    if (narrow) {
      return AppLayout(
        constrainBody: false,
        subpageOpen: _subpageOpen,
        selectedIndex: _narrowIndex,
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
              root: CompanyManagementPage(companyId: companyId, extraShellNav: _shellNav),
            ),
          ],
        ),
      );
    }

    final destinations = [
      AppNavDestination(icon: Icons.dashboard_outlined, label: l10n.navOverview),
      AppNavDestination(icon: Icons.group_outlined, label: l10n.navEmployees),
      AppNavDestination(icon: Icons.key_outlined, label: l10n.navAiKeys),
      AppNavDestination(icon: Icons.dns_outlined, label: l10n.navContainers),
      AppNavDestination(icon: Icons.view_module_outlined, label: l10n.navCabinets),
      AppNavDestination(icon: Icons.extension_outlined, label: l10n.navModules),
      for (final entry in _shellNav)
        AppNavDestination(icon: entry.icon, label: entry.label),
    ];

    final fixedPages = [
      CompanyOverviewPage(companyId: companyId),
      CompanyEmployeesPage(companyId: companyId),
      CompanyAiKeyListPage(companyId: companyId, embedded: true),
      CompanyProjectContainersPage(companyId: companyId, embedded: true),
      CompanyCabinetsPage(companyId: companyId),
      CompanyModuleListPage(companyId: companyId, embedded: true),
    ];

    final dynamicPages = [
      for (final entry in _shellNav)
        ModuleShellNavPage(entry: entry, embedded: true, companyId: companyId),
    ];

    final pages = [...fixedPages, ...dynamicPages];

    return AppLayout(
      constrainBody: false,
      subpageOpen: _subpageOpen,
      selectedIndex: _railIndex,
      onDestinationSelected: _selectRail,
      onLogoTap: () => _selectRail(0),
      destinations: destinations,
      body: IndexedStack(
        index: _railIndex,
        children: [
          for (var i = 0; i < pages.length; i++)
            AppShellBranch(
              active: _railIndex == i,
              onSubpageOpenChanged: _railIndex == i ? _onSubpageOpenChanged : null,
              root: pages[i],
            ),
        ],
      ),
    );
  }
}
