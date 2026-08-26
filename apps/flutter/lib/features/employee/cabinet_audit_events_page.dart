import 'package:flutter/material.dart';

import 'package:prodavan/core/refresh/app_auto_refresh.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Recent cabinet audit trail (L06).
class CabinetAuditEventsPage extends StatefulWidget {
  const CabinetAuditEventsPage({super.key, required this.cabinetId});

  final String cabinetId;

  @override
  State<CabinetAuditEventsPage> createState() => _CabinetAuditEventsPageState();
}

class _CabinetAuditEventsPageState extends State<CabinetAuditEventsPage> {
  late final AppAutoRefreshBinder _autoRefresh;
  bool _loading = true;
  String? _error;
  List<Map<String, dynamic>> _events = const [];

  @override
  void initState() {
    super.initState();
    _autoRefresh = AppAutoRefreshBinder(
      onTick: () => _load(silent: true),
      isActive: () => appAutoRefreshIsActive(context),
    )..attach();
    _load();
  }

  @override
  void dispose() {
    _autoRefresh.dispose();
    super.dispose();
  }

  Future<void> _load({bool silent = false}) async {
    if (!silent && mounted) {
      setState(() {
        _loading = true;
        _error = null;
      });
    }
    try {
      final events = await workContext.api.listAuditEvents(cabinetId: widget.cabinetId, limit: 100);
      if (!mounted) return;
      if (silent && appRefreshDataEquals(_events, events) && !_loading) return;
      setState(() {
        _events = events;
        _loading = false;
        _error = null;
      });
    } catch (e) {
      if (!mounted) return;
      if (silent) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return AppScaffold(
      title: Text(l10n.cabinetAuditLog),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : RefreshIndicator(
              onRefresh: _load,
              child: ListView(
                padding: EdgeInsets.all(AppSpacing.lg),
                children: [
                  if (_error != null) AppStatusBanner(severity: AppStatusSeverity.error, message: _error!),
                  if (_events.isEmpty)
                    EmptyPlaceholder(
                      title: l10n.cabinetNoAuditEventsYet,
                      fillViewport: false,
                    )
                  else
                    ..._events.map(
                      (e) => Card(
                        child: ListTile(
                          title: Text(e['event_type'] as String? ?? 'event'),
                          subtitle: Text(
                            [
                              if (e['tool_name'] != null) 'tool: ${e['tool_name']}',
                              if (e['actor_sub'] != null) 'actor: ${e['actor_sub']}',
                              if (e['created_at'] != null) '${e['created_at']}',
                            ].join(' · '),
                          ),
                          isThreeLine: true,
                        ),
                      ),
                    ),
                ],
              ),
            ),
    );
  }
}
