import 'package:flutter/material.dart';

import 'package:prodavan/core/refresh/app_auto_refresh.dart';
import 'package:prodavan/core/responsive/app_breakpoints.dart';
import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/widgets/app_layout.dart';
import 'package:prodavan/core/widgets/app_shell_branch.dart';
import 'package:prodavan/features/admin/admin_cabinet_list_page.dart';
import 'package:prodavan/features/admin/admin_management_page.dart';
import 'package:prodavan/features/admin/admin_metrics_overview_page.dart';
import 'package:prodavan/features/admin/admin_module_list_page.dart';
import 'package:prodavan/features/admin/admin_project_containers_page.dart';
import 'package:prodavan/features/admin/ai_key_list_page.dart';
import 'package:prodavan/features/admin/company_list_page.dart';
import 'package:prodavan/features/meta/meta_icon.dart';
import 'package:prodavan/features/meta/module_shell_nav_page.dart';
import 'package:prodavan/features/meta/shell_nav_loader.dart';
import 'package:prodavan/features/settings/settings_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Platform Admin shell — Overview + management sections (+ mobile Management hub).
class AdminShell extends StatefulWidget {
  const AdminShell({super.key, this.shellNavEntries});

  /// Optional injected nav entries (tests). When null, loaded from API.
  final List<ShellNavEntry>? shellNavEntries;

  @override
  State<AdminShell> createState() => _AdminShellState();
}

class _AdminShellState extends State<AdminShell> {
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
    try {
      final entries = await ShellNavLoader.loadAdmin(adminContext.api);
      if (!mounted) return;
      final maxIndex = _fixedRailCount + entries.length;
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
          AppNavDestination(icon: Icons.settings_outlined, label: l10n.settings),
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
              root: AdminManagementPage(extraShellNav: _shellNav),
            ),
            const SettingsPage(embedded: true),
          ],
        ),
      );
    }

    final destinations = [
      AppNavDestination(icon: Icons.dashboard_outlined, label: l10n.navOverview),
      AppNavDestination(icon: Icons.business_outlined, label: l10n.navCompanies),
      AppNavDestination(icon: Icons.key_outlined, label: l10n.navAiKeys),
      AppNavDestination(icon: Icons.dns_outlined, label: l10n.navContainers),
      AppNavDestination(icon: Icons.folder_outlined, label: l10n.navCabinets),
      AppNavDestination(icon: Icons.extension_outlined, label: l10n.navModules),
      for (final entry in _shellNav)
        AppNavDestination(icon: entry.icon, label: entry.label),
      AppNavDestination(icon: Icons.settings_outlined, label: l10n.settings),
    ];

    final fixedPages = const [
      AdminMetricsOverviewPage(embedded: true),
      AdminCompanyListPage(embedded: true),
      AdminAiKeyListPage(embedded: true),
      AdminProjectContainersPage(embedded: true),
      AdminCabinetListPage(embedded: true),
      AdminModuleListPage(embedded: true),
    ];

    final dynamicPages = [
      for (final entry in _shellNav)
        ModuleShellNavPage(entry: entry, embedded: true),
    ];

    final pages = [
      ...fixedPages,
      ...dynamicPages,
      const SettingsPage(embedded: true),
    ];

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
