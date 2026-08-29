import 'package:flutter/material.dart';

import 'package:prodavan/core/responsive/app_breakpoints.dart';
import 'package:prodavan/core/widgets/app_layout.dart';
import 'package:prodavan/core/widgets/app_shell_branch.dart';
import 'package:prodavan/features/employee/cabinet_management_page.dart';
import 'package:prodavan/features/employee/cabinet_module_host.dart';
import 'package:prodavan/features/employee/cabinet_nav_loader.dart';
import 'package:prodavan/features/employee/cabinet_overview_page.dart';
import 'package:prodavan/features/employee/cabinet_projects_page.dart';
import 'package:prodavan/features/employee/employee_settings_body.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Employee cabinet shell — same layout pattern as [CompanyShell].
class CabinetShell extends StatefulWidget {
  const CabinetShell({
    super.key,
    required this.cabinetId,
    required this.cabinetName,
  });

  final String cabinetId;
  final String cabinetName;

  @override
  State<CabinetShell> createState() => _CabinetShellState();
}

class _CabinetShellState extends State<CabinetShell> {
  static const _overviewIndex = 0;
  static const _projectsIndex = 1;

  int _contentIndex = _overviewIndex;
  int? _railSelected;
  int _narrowStackIndex = 1;
  bool _subpageOpen = false;
  bool _navLoading = true;
  List<CabinetNavEntry> _railEntries = const [];
  List<CabinetNavEntry> _managementEntries = const [];

  int get _railModuleCount => _railEntries.length;
  int get _settingsIndex => _projectsIndex + 1 + _railModuleCount;

  @override
  void initState() {
    super.initState();
    _loadNav();
  }

  Future<void> _loadNav() async {
    setState(() => _navLoading = true);
    try {
      final bundle = await loadCabinetNavBundle(widget.cabinetId);
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
    if (_subpageOpen == open) return;
    setState(() => _subpageOpen = open);
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
              onSubpageOpenChanged:
                  _narrowStackIndex == 0 ? _onSubpageOpenChanged : null,
              root: CabinetOverviewPage(
                cabinetId: widget.cabinetId,
                cabinetName: widget.cabinetName,
              ),
            ),
            AppShellBranch(
              active: _narrowStackIndex == 1,
              onSubpageOpenChanged:
                  _narrowStackIndex == 1 ? _onSubpageOpenChanged : null,
              root: CabinetManagementPage(
                cabinetId: widget.cabinetId,
                entries: _managementEntries,
              ),
            ),
            EmployeeSettingsBody(
              cabinetId: widget.cabinetId,
              cabinetName: widget.cabinetName,
            ),
          ],
        ),
      );
    }

    final railDestinations = [
      AppNavDestination(icon: Icons.folder_outlined, label: l10n.navProjects),
      ..._railEntries.map(
        (e) => AppNavDestination(icon: e.icon, label: e.label),
      ),
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
              children: _buildWideBranches(l10n),
            ),
    );
  }

  List<Widget> _buildWideBranches(AppLocalizations l10n) {
    final railModulePages = _railEntries
        .map(
          (e) => CabinetModuleHost(
            cabinetId: widget.cabinetId,
            entry: e,
          ),
        )
        .toList();

    final mainPages = [
      CabinetOverviewPage(
        cabinetId: widget.cabinetId,
        cabinetName: widget.cabinetName,
      ),
      CabinetProjectsPage(cabinetId: widget.cabinetId),
      ...railModulePages,
    ];

    return [
      for (var i = 0; i < mainPages.length; i++)
        AppShellBranch(
          active: _contentIndex == i,
          onSubpageOpenChanged: _contentIndex == i ? _onSubpageOpenChanged : null,
          root: mainPages[i],
        ),
      EmployeeSettingsBody(
        cabinetId: widget.cabinetId,
        cabinetName: widget.cabinetName,
      ),
    ];
  }
}
