import 'package:flutter/material.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';

import 'package:prodavan/core/refresh/app_auto_refresh.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Platform cabinet.* MCP tools list (L05/L06 interpreter).
class CabinetToolsTabPage extends StatefulWidget {
  const CabinetToolsTabPage({super.key, required this.cabinetId});

  final String cabinetId;

  @override
  State<CabinetToolsTabPage> createState() => _CabinetToolsTabPageState();
}

class _CabinetToolsTabPageState extends State<CabinetToolsTabPage> {
  late final AppAutoRefreshBinder _autoRefresh;
  bool _loading = true;
  Object? _error;
  List<Map<String, dynamic>> _tools = const [];

  @override
  void initState() {
    super.initState();
    _autoRefresh = AppAutoRefreshBinder(
      onTick: () => _reload(silent: true),
      isActive: () => appAutoRefreshIsActive(context),
    )..attach();
    _reload();
  }

  @override
  void dispose() {
    _autoRefresh.dispose();
    super.dispose();
  }

  Future<void> _reload({bool silent = false}) async {
    if (!silent && mounted) {
      setState(() {
        _loading = true;
        _error = null;
      });
    }
    try {
      final tools = await workContext.api.listCabinetMcpTools(widget.cabinetId);
      if (!mounted) return;
      if (silent && appRefreshDataEquals(_tools, tools) && !_loading) return;
      setState(() {
        _tools = tools;
        _loading = false;
        _error = null;
      });
    } catch (e) {
      if (!mounted) return;
      if (silent) return;
      setState(() {
        _error = e;
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (_error != null) AppStatusBanner(severity: AppStatusSeverity.error, message: AppErrors.localize(context, _error!)),
        Expanded(
          child: _tools.isEmpty
              ? EmptyPlaceholder(title: l10n.cabinetNoMcpTools)
              : ListView.separated(
                  padding: const EdgeInsets.all(12),
                  itemCount: _tools.length,
                  separatorBuilder: (_, __) => const Divider(height: 1),
                  itemBuilder: (context, index) {
                    final tool = _tools[index];
                    final name = tool['name'] as String? ?? tool['tool'] as String? ?? 'tool';
                    final description = tool['description'] as String? ?? '';
                    return ListTile(
                      leading: const Icon(Icons.build_outlined),
                      title: Text(name),
                      subtitle: description.isEmpty ? null : Text(description),
                    );
                  },
                ),
        ),
      ],
    );
  }
}
