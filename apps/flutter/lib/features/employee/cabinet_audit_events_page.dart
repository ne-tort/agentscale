import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/empty_state.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';

/// Recent cabinet audit trail (L06).
class CabinetAuditEventsPage extends StatefulWidget {
  const CabinetAuditEventsPage({super.key, required this.cabinetId});

  final String cabinetId;

  @override
  State<CabinetAuditEventsPage> createState() => _CabinetAuditEventsPageState();
}

class _CabinetAuditEventsPageState extends State<CabinetAuditEventsPage> {
  bool _loading = true;
  String? _error;
  List<Map<String, dynamic>> _events = const [];

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final events = await workContext.api.listAuditEvents(cabinetId: widget.cabinetId, limit: 100);
      if (!mounted) return;
      setState(() {
        _events = events;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return AppScaffold(
      title: const Text('Audit log'),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : RefreshIndicator(
              onRefresh: _load,
              child: ListView(
                padding: const EdgeInsets.all(AppSpacing.lg),
                children: [
                  if (_error != null) InlineErrorBanner(message: _error!),
                  if (_events.isEmpty)
                    const EmptyState(title: 'No audit events yet.')
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
