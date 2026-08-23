import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
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

class _DynamicCabinetShellState extends State<DynamicCabinetShell> with SingleTickerProviderStateMixin {
  TabController? _tabs;
  List<Map<String, dynamic>> _metaTabs = const [];
  String? _error;
  int _metaEpoch = 0;

  @override
  void initState() {
    super.initState();
    workContext.enterCabinet(widget.cabinetId);
    workContext.addListener(_onWorkContext);
    _metaEpoch = workContext.cabinetMetaEpoch;
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
        _tabs?.dispose();
        _tabs = TabController(length: tabs.isEmpty ? 1 : tabs.length, vsync: this);
        _error = null;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = e.toString());
    }
  }

  @override
  void dispose() {
    workContext.removeListener(_onWorkContext);
    _tabs?.dispose();
    super.dispose();
  }

  Widget _tabBody(Map<String, dynamic> tab) {
    return CabinetTabHost(
      cabinetId: widget.cabinetId,
      cabinetName: widget.cabinetName,
      tab: tab,
    );
  }

  @override
  Widget build(BuildContext context) {
    final tabs = _metaTabs;
    return AppScaffold(
      title: Text(widget.cabinetName),
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
