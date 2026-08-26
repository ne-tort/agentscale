import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/widgets/app_collection_view_mode.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';
import 'package:prodavan/features/employee/cabinet_tab_host.dart';

/// Dynamic cabinet shell — tabs from L06 meta (L05).
class DynamicCabinetShell extends StatefulWidget {
  const DynamicCabinetShell({
    super.key,
    required this.cabinetId,
    required this.cabinetName,
  });

  final String cabinetId;
  final String cabinetName;

  @override
  State<DynamicCabinetShell> createState() => _DynamicCabinetShellState();
}

class _DynamicCabinetShellState extends State<DynamicCabinetShell>
    with SingleTickerProviderStateMixin {
  static const _projectsViewPageKey = 'cabinet.projects';

  final _projectsViewMode = AppCollectionViewModeStore(_projectsViewPageKey);
  TabController? _tabs;
  List<Map<String, dynamic>> _metaTabs = const [];
  String? _error;
  int _metaEpoch = 0;
  int _tabIndex = 0;

  @override
  void initState() {
    super.initState();
    workContext.enterCabinet(widget.cabinetId);
    workContext.addListener(_onWorkContext);
    _metaEpoch = workContext.cabinetMetaEpoch;
    _projectsViewMode.load();
    _loadTabs();
  }

  void _onWorkContext() {
    if (workContext.cabinetMetaEpoch != _metaEpoch) {
      _metaEpoch = workContext.cabinetMetaEpoch;
      _loadTabs();
    }
  }

  Future<void> _loadTabs() async {
    try {
      final tabs = await workContext.api.listMetaTabs(widget.cabinetId);
      if (!mounted) return;
      setState(() {
        _metaTabs = tabs;
        _tabs?.removeListener(_onTabChanged);
        _tabs?.dispose();
        _tabs = TabController(length: tabs.isEmpty ? 1 : tabs.length, vsync: this);
        _tabs!.addListener(_onTabChanged);
        _tabIndex = 0;
        _error = null;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = e.toString());
    }
  }

  void _onTabChanged() {
    if (_tabs == null || _tabs!.indexIsChanging) return;
    if (_tabIndex != _tabs!.index) {
      setState(() => _tabIndex = _tabs!.index);
    }
  }

  bool get _isProjectsTab {
    if (_metaTabs.isEmpty || _tabIndex < 0 || _tabIndex >= _metaTabs.length) {
      return false;
    }
    final slug = _metaTabs[_tabIndex]['view_slug'] as String? ?? '';
    return slug == 'projects' || slug == 'chat';
  }

  @override
  void dispose() {
    workContext.removeListener(_onWorkContext);
    _tabs?.removeListener(_onTabChanged);
    _tabs?.dispose();
    _projectsViewMode.dispose();
    super.dispose();
  }

  Widget _tabBody(Map<String, dynamic> tab) {
    return CabinetTabHost(
      cabinetId: widget.cabinetId,
      cabinetName: widget.cabinetName,
      tab: tab,
      projectsViewModeStore: _projectsViewMode,
    );
  }

  @override
  Widget build(BuildContext context) {
    final tabs = _metaTabs;
    return AppScaffold(
      title: Text(widget.cabinetName),
      actions: [
        if (_isProjectsTab) AppCollectionViewModeButton(store: _projectsViewMode),
      ],
      bottom: tabs.isEmpty || _tabs == null
          ? null
          : TabBar(
              controller: _tabs,
              isScrollable: true,
              tabs: [for (final t in tabs) Tab(text: t['title'] as String? ?? 'Tab')],
            ),
      body: Column(
        children: [
          if (_error != null) InlineErrorBanner(message: _error!),
          Expanded(
            child: tabs.isEmpty || _tabs == null
                ? const Center(child: CircularProgressIndicator())
                : TabBarView(
                    controller: _tabs,
                    children: [for (final t in tabs) _tabBody(t)],
                  ),
          ),
        ],
      ),
    );
  }
}
