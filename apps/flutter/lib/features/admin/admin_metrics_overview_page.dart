import 'package:flutter/material.dart';

import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_section_header.dart';
import 'package:prodavan/core/widgets/empty_state.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';
import 'package:prodavan/core/widgets/stat_tile.dart';
import 'package:prodavan/features/admin/company_detail_page.dart';

enum _AlertKind { noKeys, keyExpiring, highUsage, subscriptionExpiring, subscriptionExpired }

class _OverviewAlert {
  const _OverviewAlert({
    required this.kind,
    required this.companyId,
    required this.companyName,
    required this.subtitle,
  });

  final _AlertKind kind;
  final String companyId;
  final String companyName;
  final String subtitle;
}

/// Platform-wide metrics overview — «Сводка» tab (L04 / metrics.md).
class AdminMetricsOverviewPage extends StatefulWidget {
  const AdminMetricsOverviewPage({super.key, this.embedded = false});

  final bool embedded;

  @override
  State<AdminMetricsOverviewPage> createState() => _AdminMetricsOverviewPageState();
}

class _AdminMetricsOverviewPageState extends State<AdminMetricsOverviewPage> {
  bool _loading = true;
  String? _error;
  List<Map<String, dynamic>> _items = const [];
  List<_OverviewAlert> _alerts = const [];

  @override
  void initState() {
    super.initState();
    _reload();
  }

  Future<void> _reload() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final items = await adminContext.api.listCompaniesMetrics();
      if (!mounted) return;
      setState(() {
        _items = items;
        _alerts = _buildAlerts(items);
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

  List<_OverviewAlert> _buildAlerts(List<Map<String, dynamic>> companies) {
    final alerts = <_OverviewAlert>[];
    for (final c in companies) {
      final id = _companyId(c);
      final name = c['name'] as String? ?? id;
      final employees = _asInt(c['employees_total']);
      final bound = _asInt(c['ai_keys_bound']);
      final expiring = _asInt(c['ai_keys_expiring_soon']);
      if (employees > 0 && bound == 0) {
        alerts.add(
          _OverviewAlert(
            kind: _AlertKind.noKeys,
            companyId: id,
            companyName: name,
            subtitle: 'Agent sessions will fail with NO_AI_KEY',
          ),
        );
      }
      if (expiring > 0) {
        final next = c['next_key_renewal_at'];
        alerts.add(
          _OverviewAlert(
            kind: _AlertKind.keyExpiring,
            companyId: id,
            companyName: name,
            subtitle: '$expiring key(s) renewing soon${next != null ? ' · next $next' : ''}',
          ),
        );
      }
      if (c['high_agent_usage'] == true) {
        alerts.add(
          _OverviewAlert(
            kind: _AlertKind.highUsage,
            companyId: id,
            companyName: name,
            subtitle: 'Agent tokens ${_asInt(c['agent_tokens_used'])} above platform threshold',
          ),
        );
      }
      if (c['subscription_lifetime'] != true && c['subscription_expired'] == true) {
        final ends = c['subscription_ends_at'];
        alerts.add(
          _OverviewAlert(
            kind: _AlertKind.subscriptionExpired,
            companyId: id,
            companyName: name,
            subtitle: 'Subscription ended${ends != null ? ' · $ends' : ''}',
          ),
        );
      } else if (c['subscription_lifetime'] != true && c['subscription_expiring_soon'] == true) {
        final ends = c['subscription_ends_at'];
        alerts.add(
          _OverviewAlert(
            kind: _AlertKind.subscriptionExpiring,
            companyId: id,
            companyName: name,
            subtitle: 'Subscription ends soon${ends != null ? ' · $ends' : ''}',
          ),
        );
      }
    }
    return alerts;
  }

  String _companyId(Map<String, dynamic> item) =>
      item['company_id'] as String? ?? item['id'] as String? ?? '';

  int _asInt(Object? v) {
    if (v is int) return v;
    if (v is num) return v.toInt();
    return 0;
  }

  void _openAlertCompany(_OverviewAlert alert) {
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => AdminCompanyDetailPage(
          companyId: alert.companyId,
          companyName: alert.companyName,
        ),
      ),
    );
  }

  int _sum(String key) {
    var total = 0;
    for (final item in _items) {
      total += _asInt(item[key]);
    }
    return total;
  }

  String _cell(Map<String, dynamic> item, String key) {
    final v = item[key];
    if (v == null) return '—';
    return '$v';
  }

  void _openCompany(AppEntityRow row) {
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => AdminCompanyDetailPage(companyId: row.id, companyName: row.title),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final rows = _items.map((item) {
      final companyId = _companyId(item);
      final name = item['name'] as String? ?? companyId;
      final cabinets = '${_cell(item, 'active_cabinets')} / ${_cell(item, 'cabinets_quota')}';
      return AppEntityRow(
        id: companyId,
        title: name,
        subtitle: '$cabinets cabinets · ${_cell(item, 'projects_total')} projects',
        cells: {
          'employees': _cell(item, 'employees_total'),
          'cabinets': cabinets,
          'projects': _cell(item, 'projects_total'),
          'tokens': _cell(item, 'agent_tokens_used'),
        },
      );
    }).toList();

    return AppScaffold(
      title: const Text('Overview'),
      actions: [
        IconButton(onPressed: _reload, icon: const Icon(Icons.refresh)),
      ],
      body: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (_error != null) InlineErrorBanner(message: _error!),
          if (!_loading && _alerts.isNotEmpty) ...[
            Padding(
              padding: const EdgeInsets.fromLTRB(AppSpacing.md, AppSpacing.md, AppSpacing.md, 0),
              child: const AppSectionHeader(title: 'Alerts'),
            ),
            ..._alerts.map(
              (a) => ListTile(
                leading: Icon(
                  switch (a.kind) {
                    _AlertKind.noKeys => Icons.warning_amber_outlined,
                    _AlertKind.keyExpiring => Icons.schedule_outlined,
                    _AlertKind.highUsage => Icons.trending_up,
                    _AlertKind.subscriptionExpiring => Icons.event_outlined,
                    _AlertKind.subscriptionExpired => Icons.event_busy_outlined,
                  },
                  color: switch (a.kind) {
                    _AlertKind.noKeys => Colors.orange,
                    _AlertKind.keyExpiring => Colors.deepOrange,
                    _AlertKind.highUsage => Colors.redAccent,
                    _AlertKind.subscriptionExpiring => Colors.deepOrange,
                    _AlertKind.subscriptionExpired => Colors.red,
                  },
                ),
                title: Text(
                  switch (a.kind) {
                    _AlertKind.noKeys => '${a.companyName}: no AI keys bound',
                    _AlertKind.keyExpiring => '${a.companyName}: AI key renewal soon',
                    _AlertKind.highUsage => '${a.companyName}: high agent token usage',
                    _AlertKind.subscriptionExpiring => '${a.companyName}: subscription expiring',
                    _AlertKind.subscriptionExpired => '${a.companyName}: subscription expired',
                  },
                ),
                subtitle: Text(a.subtitle),
                trailing: const Icon(Icons.chevron_right),
                onTap: () => _openAlertCompany(a),
              ),
            ),
          ],
          if (!_loading && _items.isNotEmpty) ...[
            Padding(
              padding: const EdgeInsets.fromLTRB(AppSpacing.md, AppSpacing.md, AppSpacing.md, 0),
              child: const AppSectionHeader(title: 'Platform totals'),
            ),
            Padding(
              padding: const EdgeInsets.all(AppSpacing.md),
              child: Wrap(
                spacing: AppSpacing.sm,
                runSpacing: AppSpacing.sm,
                children: [
                  SizedBox(
                    width: 150,
                    child: StatTile(label: 'Companies', value: '${_items.length}'),
                  ),
                  SizedBox(
                    width: 150,
                    child: StatTile(label: 'Employees', value: '${_sum('employees_total')}'),
                  ),
                  SizedBox(
                    width: 150,
                    child: StatTile(label: 'Projects', value: '${_sum('projects_total')}'),
                  ),
                  SizedBox(
                    width: 150,
                    child: StatTile(label: 'Agent tokens', value: '${_sum('agent_tokens_used')}'),
                  ),
                ],
              ),
            ),
          ],
          Expanded(
            child: AppEntityCollection(
              loading: _loading,
              rows: rows,
              columns: const [
                AppEntityColumn(id: 'name', label: 'Company'),
                AppEntityColumn(id: 'employees', label: 'Employees'),
                AppEntityColumn(id: 'cabinets', label: 'Cabinets'),
                AppEntityColumn(id: 'projects', label: 'Projects'),
                AppEntityColumn(id: 'tokens', label: 'Tokens'),
              ],
              onOpen: _openCompany,
              empty: EmptyState(
                title: 'No companies yet',
                subtitle: 'Create a company to see platform metrics',
              ),
            ),
          ),
        ],
      ),
    );
  }
}
