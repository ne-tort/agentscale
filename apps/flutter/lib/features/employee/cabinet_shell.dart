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
  int _contentIndex = 0;
  int _railSelected = 0;
  bool _subpageOpen = false;
  bool _navLoading = true;
  List<CabinetNavEntry> _moduleEntries = const [];

  int get _moduleCount => _moduleEntries.length;
  int get _settingsIndex => 1 + _moduleCount + 1;

  @override
  void initState() {
    super.initState();
    _loadNav();
  }

  Future<void> _loadNav() async {
    setState(() => _navLoading = true);
    try {
      final entries = await loadCabinetNavEntries(widget.cabinetId);
      if (!mounted) return;
      setState(() {
        _moduleEntries = entries;
        _navLoading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _moduleEntries = const [];
        _navLoading = false;
      });
    }
  }

  void _onSubpageOpenChanged(bool open) {
    if (_subpageOpen == open) return;
    setState(() => _subpageOpen = open);
  }

  void _selectRail(int railIndex) {
    setState(() {
      _railSelected = railIndex;
      _contentIndex = railIndex + 1;
    });
  }

  void _selectSettingsWide() {
    setState(() {
      _contentIndex = _settingsIndex;
      _railSelected = -1;
    });
  }

  void _goOverview() {
    setState(() {
      _contentIndex = 0;
      _railSelected = -1;
    });
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final wide = AppBreakpoints.isWide(context);

    final moduleDestinations = _moduleEntries
        .map(
          (e) => AppNavDestination(icon: e.icon, label: e.label),
        )
        .toList();

    final destinations = [
      AppNavDestination(icon: Icons.folder_outlined, label: l10n.navProjects),
      ...moduleDestinations,
      AppNavDestination(icon: Icons.tune_outlined, label: l10n.navManagement),
    ];

    final settingsDest = AppNavDestination(
      icon: Icons.settings_outlined,
      label: l10n.settings,
    );

    if (!wide) {
      return AppLayout(
        constrainBody: false,
        subpageOpen: _subpageOpen,
        selectedIndex: _contentIndex.clamp(0, _settingsIndex),
        onDestinationSelected: (i) {
          if (i == _settingsIndex) {
            _selectSettingsWide();
          } else {
            setState(() {
              _contentIndex = i;
              _railSelected = i > 0 ? i - 1 : -1;
            });
          }
        },
        onLogoTap: _goOverview,
        destinations: [
          AppNavDestination(icon: Icons.dashboard_outlined, label: l10n.navOverview),
          ...destinations,
        ],
        trailingDestination: settingsDest,
        trailingSelected: _contentIndex == _settingsIndex,
        onTrailingSelected: _selectSettingsWide,
        body: IndexedStack(
          index: _contentIndex.clamp(0, _settingsIndex),
          children: _buildBranches(l10n),
        ),
      );
    }

    return AppLayout(
      constrainBody: false,
      subpageOpen: _subpageOpen,
      selectedIndex: _railSelected,
      trailingDestination: settingsDest,
      trailingSelected: _contentIndex == _settingsIndex,
      onTrailingSelected: _selectSettingsWide,
      onDestinationSelected: _selectRail,
      onLogoTap: _goOverview,
      destinations: destinations,
      body: _navLoading
          ? const Center(child: CircularProgressIndicator())
          : IndexedStack(
              index: _contentIndex,
              children: _buildBranches(l10n),
            ),
    );
  }

  List<Widget> _buildBranches(AppLocalizations l10n) {
    final modulePages = _moduleEntries
        .map(
          (e) => CabinetModuleHost(
            cabinetId: widget.cabinetId,
            entry: e,
          ),
        )
        .toList();

    final mainPages = [
      CabinetOverviewPage(cabinetId: widget.cabinetId, cabinetName: widget.cabinetName),
      CabinetProjectsPage(cabinetId: widget.cabinetId),
      ...modulePages,
      CabinetManagementPage(cabinetId: widget.cabinetId),
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
