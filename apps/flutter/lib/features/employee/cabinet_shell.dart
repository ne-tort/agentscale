import 'package:flutter/material.dart';

import 'package:prodavan/core/responsive/app_breakpoints.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_layout.dart';
import 'package:prodavan/core/widgets/app_shell_branch.dart';
import 'package:prodavan/features/employee/cabinet_chats_page.dart';
import 'package:prodavan/features/employee/cabinet_chats_rail.dart';
import 'package:prodavan/features/employee/cabinet_management_page.dart';
import 'package:prodavan/features/employee/cabinet_module_host.dart';
import 'package:prodavan/features/employee/cabinet_nav_loader.dart';
import 'package:prodavan/features/employee/cabinet_overview_page.dart';
import 'package:prodavan/features/employee/cabinet_projects_page.dart';
import 'package:prodavan/features/employee/employee_settings_body.dart';
import 'package:prodavan/features/employee/project_workspace_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Employee cabinet shell — Projects + Chats rail + optional modules + hubs.
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
  List<CabinetNavEntry> _dataEntries = const [];

  bool _newChatEnabled = false;
  List<Map<String, dynamic>> _pinnedChats = const [];
  List<Map<String, dynamic>> _projectChats = const [];
  String? _activeSessionId;

  bool get _showManagement => true;
  bool get _showData => true;

  int get _managementContentIndex => _projectsIndex + 1 + _railEntries.length;
  int get _dataContentIndex =>
      _managementContentIndex + (_showManagement ? 1 : 0);
  int get _settingsIndex => _dataContentIndex + (_showData ? 1 : 0);

  /// Content indices for each wide rail destination (parallel to destinations).
  List<int> get _wideDestContentIndices {
    final indices = <int>[_projectsIndex];
    for (var i = 0; i < _railEntries.length; i++) {
      indices.add(_projectsIndex + 1 + i);
    }
    if (_showManagement) indices.add(_managementContentIndex);
    if (_showData) indices.add(_dataContentIndex);
    return indices;
  }

  @override
  void initState() {
    super.initState();
    workContext.enterCabinet(widget.cabinetId);
    workContext.addListener(_onWorkContext);
    _loadNav();
    _loadSelectionAndSidebar();
  }

  @override
  void dispose() {
    workContext.removeListener(_onWorkContext);
    super.dispose();
  }

  String? _lastKnownSelectedProjectId;
  int _lastProjectLifecycleEpoch = 0;

  void _onWorkContext() {
    if (!mounted) return;
    final selected = workContext.selectedProjectId;
    final lifecycle = workContext.projectLifecycleEpoch;
    if (selected != _lastKnownSelectedProjectId) {
      _lastKnownSelectedProjectId = selected;
      _lastProjectLifecycleEpoch = lifecycle;
      _reloadSidebar();
      _loadNav();
      return;
    }
    if (lifecycle != _lastProjectLifecycleEpoch) {
      _lastProjectLifecycleEpoch = lifecycle;
      _reloadSidebar();
      _loadNav();
      return;
    }
    setState(() {});
  }

  Future<void> _loadNav() async {
    setState(() => _navLoading = true);
    try {
      final bundle = await loadCabinetNavBundle(
        widget.cabinetId,
        projectId: workContext.selectedProjectId,
      );
      if (!mounted) return;
      setState(() {
        _railEntries = bundle.rail;
        _managementEntries = bundle.management;
        _dataEntries = bundle.data;
        _navLoading = false;
        if (_contentIndex > _settingsIndex) {
          _contentIndex = _overviewIndex;
          _railSelected = null;
        }
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _railEntries = const [];
        _managementEntries = const [];
        _dataEntries = const [];
        _navLoading = false;
      });
    }
  }

  Future<void> _loadSelectionAndSidebar() async {
    try {
      await workContext.loadProjectSelection(widget.cabinetId);
      _lastKnownSelectedProjectId = workContext.selectedProjectId;
      _lastProjectLifecycleEpoch = workContext.projectLifecycleEpoch;
      await _reloadSidebar();
    } catch (_) {
      // Sidebar is best-effort; projects page still works.
    }
  }

  Future<void> _reloadSidebar() async {
    try {
      final body = await workContext.api.getChatsSidebar(widget.cabinetId);
      if (!mounted) return;
      final pinned = body['pinned'];
      final projectChats = body['project_chats'];
      setState(() {
        _newChatEnabled = body['new_chat_enabled'] == true;
        _pinnedChats = pinned is List ? pinned.cast<Map<String, dynamic>>() : const [];
        _projectChats =
            projectChats is List ? projectChats.cast<Map<String, dynamic>>() : const [];
        final selected = body['selected_project_id'] as String?;
        if (selected != workContext.selectedProjectId) {
          workContext.setSelectedProjectId(selected);
        }
      });
    } catch (_) {
      /* ignore */
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

  void _selectRail(int destIndex) {
    final indices = _wideDestContentIndices;
    if (destIndex < 0 || destIndex >= indices.length) return;
    setState(() {
      _railSelected = destIndex;
      _contentIndex = indices[destIndex];
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
      _narrowStackIndex = _narrowSettingsStackIndex;
      _subpageOpen = false;
    });
  }

  /// Narrow stack: 0 overview, 1 projects, 2 management?, 3 data?, last settings.
  int get _narrowManagementStackIndex => 2;
  int get _narrowDataStackIndex => 2 + (_showManagement ? 1 : 0);
  int get _narrowSettingsStackIndex =>
      2 + (_showManagement ? 1 : 0) + (_showData ? 1 : 0);

  List<({int stackIndex, AppNavDestination dest})> _narrowDestinations(
    AppLocalizations l10n,
  ) {
    return [
      (
        stackIndex: 1,
        dest: AppNavDestination(icon: Icons.folder_outlined, label: l10n.navProjects),
      ),
      (
        stackIndex: 0,
        dest: AppNavDestination(icon: Icons.dashboard_outlined, label: l10n.navOverview),
      ),
      if (_showManagement)
        (
          stackIndex: _narrowManagementStackIndex,
          dest: AppNavDestination(icon: Icons.apps_outlined, label: l10n.navManagement),
        ),
      if (_showData)
        (
          stackIndex: _narrowDataStackIndex,
          dest: AppNavDestination(
            icon: Icons.table_chart_outlined,
            label: l10n.navData,
          ),
        ),
    ];
  }

  int? _narrowSelectedDestIndex(AppLocalizations l10n) {
    if (_narrowStackIndex == _narrowSettingsStackIndex) return null;
    final dests = _narrowDestinations(l10n);
    for (var i = 0; i < dests.length; i++) {
      if (dests[i].stackIndex == _narrowStackIndex) return i;
    }
    return 0;
  }

  void _onNarrowDestinationSelected(int index, AppLocalizations l10n) {
    final dests = _narrowDestinations(l10n);
    if (index < 0 || index >= dests.length) return;
    setState(() {
      _narrowStackIndex = dests[index].stackIndex;
      _subpageOpen = false;
    });
  }

  Future<void> _onProjectSelected() async {
    await _reloadSidebar();
  }

  Future<void> _openChat(Map<String, dynamic> chat) async {
    final sessionId = chat['session_id'] as String?;
    final projectId = chat['project_id'] as String?;
    if (sessionId == null || projectId == null) return;
    final projectName = chat['project_name'] as String? ?? projectId;
    setState(() => _activeSessionId = sessionId);
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => ProjectWorkspacePage(
          cabinetId: widget.cabinetId,
          projectId: projectId,
          projectName: projectName,
          sessionId: sessionId,
          initialTitle: chat['title'] as String?,
          initiallyPinned: chat['pinned'] == true,
        ),
      ),
    );
    if (!mounted) return;
    setState(() => _activeSessionId = null);
    await _reloadSidebar();
  }

  Future<void> _newChat() async {
    final projectId = workContext.selectedProjectId;
    if (projectId == null || !_newChatEnabled) return;
    try {
      final created = await workContext.api.createAgentSession(projectId: projectId);
      final sessionId = created['id'] as String?;
      if (sessionId == null) return;
      String name = projectId;
      try {
        final p = await workContext.api.getProject(projectId);
        name = p['name'] as String? ?? projectId;
      } catch (_) {}
      if (!mounted) return;
      setState(() => _activeSessionId = sessionId);
      await Navigator.of(context).push<void>(
        MaterialPageRoute<void>(
          builder: (_) => ProjectWorkspacePage(
            cabinetId: widget.cabinetId,
            projectId: projectId,
            projectName: name,
            sessionId: sessionId,
            initialTitle: created['title'] as String?,
          ),
        ),
      );
      if (!mounted) return;
      setState(() => _activeSessionId = null);
      await _reloadSidebar();
    } catch (e) {
      if (!mounted) return;
      AppErrors.showSnack(context, e);
    }
  }

  void _openNarrowChats() {
    Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => CabinetChatsPage(
          newChatEnabled: _newChatEnabled,
          pinned: _pinnedChats,
          projectChats: _projectChats,
          activeSessionId: _activeSessionId,
          onNewChat: _newChatEnabled ? _newChat : null,
          onOpenChat: _openChat,
        ),
      ),
    );
  }

  Widget _chatsRail({required bool extended}) {
    return CabinetChatsRail(
      extended: extended,
      newChatEnabled: _newChatEnabled,
      pinned: _pinnedChats,
      projectChats: _projectChats,
      activeSessionId: _activeSessionId,
      onNewChat: _newChatEnabled ? _newChat : null,
      onOpenChat: _openChat,
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final narrow = AppBreakpoints.isNarrow(context);
    final settingsDest = AppNavDestination(
      icon: Icons.settings_outlined,
      label: l10n.settings,
    );
    final expanded = AppBreakpoints.railExtended(context, subpageOpen: _subpageOpen);

    if (narrow) {
      final narrowDests = _narrowDestinations(l10n);
      return AppLayout(
        constrainBody: false,
        subpageOpen: _subpageOpen,
        selectedIndex: _narrowSelectedDestIndex(l10n),
        trailingDestination: settingsDest,
        trailingSelected: _narrowStackIndex == _narrowSettingsStackIndex,
        onTrailingSelected: _selectNarrowSettings,
        onDestinationSelected: (i) => _onNarrowDestinationSelected(i, l10n),
        onLogoTap: _goOverview,
        destinations: [for (final d in narrowDests) d.dest],
        actions: [
          IconButton(
            icon: const Icon(Icons.forum_outlined),
            tooltip: l10n.navChats,
            onPressed: _openNarrowChats,
          ),
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
              root: CabinetProjectsPage(
                cabinetId: widget.cabinetId,
                onSelectionChanged: _onProjectSelected,
              ),
            ),
            if (_showManagement)
              AppShellBranch(
                active: _narrowStackIndex == _narrowManagementStackIndex,
                onSubpageOpenChanged: _narrowStackIndex == _narrowManagementStackIndex
                    ? _onSubpageOpenChanged
                    : null,
                root: CabinetManagementPage(
                  cabinetId: widget.cabinetId,
                  projectId: workContext.selectedProjectId,
                  entries: _managementEntries,
                ),
              ),
            if (_showData)
              AppShellBranch(
                active: _narrowStackIndex == _narrowDataStackIndex,
                onSubpageOpenChanged:
                    _narrowStackIndex == _narrowDataStackIndex ? _onSubpageOpenChanged : null,
                root: CabinetDataPage(
                  cabinetId: widget.cabinetId,
                  projectId: workContext.selectedProjectId,
                  entries: _dataEntries,
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
      if (_showManagement)
        AppNavDestination(icon: Icons.apps_outlined, label: l10n.navManagement),
      if (_showData)
        AppNavDestination(icon: Icons.table_chart_outlined, label: l10n.navData),
    ];

    final wideSelected = () {
      if (_contentIndex == _overviewIndex || _contentIndex == _settingsIndex) {
        return null;
      }
      final idx = _wideDestContentIndices.indexOf(_contentIndex);
      return idx >= 0 ? idx : _railSelected;
    }();

    return AppLayout(
      constrainBody: false,
      subpageOpen: _subpageOpen,
      selectedIndex: wideSelected,
      trailingDestination: settingsDest,
      trailingSelected: _contentIndex == _settingsIndex,
      onTrailingSelected: _selectSettingsWide,
      onDestinationSelected: _selectRail,
      onLogoTap: _goOverview,
      destinations: railDestinations,
      railExtra: _chatsRail(extended: expanded),
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
            embedded: true,
          ),
        )
        .toList();

    final mainPages = <Widget>[
      CabinetOverviewPage(
        cabinetId: widget.cabinetId,
        cabinetName: widget.cabinetName,
      ),
      CabinetProjectsPage(
        cabinetId: widget.cabinetId,
        onSelectionChanged: _onProjectSelected,
      ),
      ...railModulePages,
      if (_showManagement)
        CabinetManagementPage(
          cabinetId: widget.cabinetId,
          projectId: workContext.selectedProjectId,
          entries: _managementEntries,
          embedded: true,
        ),
      if (_showData)
        CabinetDataPage(
          cabinetId: widget.cabinetId,
          projectId: workContext.selectedProjectId,
          entries: _dataEntries,
          embedded: true,
        ),
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
