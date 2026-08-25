import 'package:flutter/material.dart';

import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_section_header.dart';
import 'package:prodavan/core/widgets/empty_state.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';
import 'package:prodavan/core/widgets/stat_tile.dart';
import 'package:prodavan/features/admin/company_detail_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

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
    final l10n = AppLocalizations.of(context);
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
            subtitle: l10n.adminAlertNoKeysSubtitle,
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
            subtitle: next != null
                ? l10n.adminAlertKeysRenewingSoonNext('$expiring', '$next')
                : l10n.adminAlertKeysRenewingSoon('$expiring'),
          ),
        );
      }
      if (c['high_agent_usage'] == true) {
        alerts.add(
          _OverviewAlert(
            kind: _AlertKind.highUsage,
            companyId: id,
            companyName: name,
            subtitle: l10n.adminAlertTokensAboveThreshold('${_asInt(c['agent_tokens_used'])}'),
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
            subtitle: ends != null
                ? l10n.adminAlertSubscriptionEndedAt('$ends')
                : l10n.adminAlertSubscriptionEnded,
          ),
        );
      } else if (c['subscription_lifetime'] != true && c['subscription_expiring_soon'] == true) {
        final ends = c['subscription_ends_at'];
        alerts.add(
          _OverviewAlert(
            kind: _AlertKind.subscriptionExpiring,
            companyId: id,
            companyName: name,
            subtitle: ends != null
                ? l10n.adminAlertSubscriptionEndsSoonAt('$ends')
                : l10n.adminAlertSubscriptionEndsSoon,
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
    if (v == null) return AppLocalizations.of(context).commonEmDash;
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
    final l10n = AppLocalizations.of(context);
    final rows = _items.map((item) {
      final companyId = _companyId(item);
      final name = item['name'] as String? ?? companyId;
      final cabinets = '${_cell(item, 'active_cabinets')} / ${_cell(item, 'cabinets_quota')}';
      return AppEntityRow(
        id: companyId,
        title: name,
        subtitle: l10n.adminCabinetsProjectsSubtitle(cabinets, _cell(item, 'projects_total')),
        cells: {
          'employees': _cell(item, 'employees_total'),
          'cabinets': cabinets,
          'projects': _cell(item, 'projects_total'),
          'tokens': _cell(item, 'agent_tokens_used'),
        },
      );
    }).toList();

    final colors = context.appColors;
    return AppScaffold(
      title: Text(l10n.commonOverview),
      actions: [
        IconButton(onPressed: _reload, icon: const Icon(Icons.refresh)),
      ],
      body: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (_error != null) InlineErrorBanner(message: _error!),
          if (!_loading && _alerts.isNotEmpty) ...[
            Padding(
              padding: EdgeInsets.fromLTRB(AppSpacing.md, AppSpacing.md, AppSpacing.md, 0),
              child: AppSectionHeader(title: l10n.adminAlerts),
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
                    _AlertKind.noKeys => colors.warning,
                    _AlertKind.keyExpiring => colors.warning,
                    _AlertKind.highUsage => colors.danger,
                    _AlertKind.subscriptionExpiring => colors.warning,
                    _AlertKind.subscriptionExpired => colors.danger,
                  },
                ),
                title: Text(
                  switch (a.kind) {
                    _AlertKind.noKeys => l10n.adminAlertNoKeysTitle(a.companyName),
                    _AlertKind.keyExpiring => l10n.adminAlertKeyRenewalTitle(a.companyName),
                    _AlertKind.highUsage => l10n.adminAlertHighUsageTitle(a.companyName),
                    _AlertKind.subscriptionExpiring => l10n.adminAlertSubExpiringTitle(a.companyName),
                    _AlertKind.subscriptionExpired => l10n.adminAlertSubExpiredTitle(a.companyName),
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
              padding: EdgeInsets.fromLTRB(AppSpacing.md, AppSpacing.md, AppSpacing.md, 0),
              child: AppSectionHeader(title: l10n.adminPlatformTotals),
            ),
            Padding(
              padding: const EdgeInsets.all(AppSpacing.md),
              child: Wrap(
                spacing: AppSpacing.sm,
                runSpacing: AppSpacing.sm,
                children: [
                  SizedBox(
                    width: 150,
                    child: StatTile(label: l10n.navCompanies, value: '${_items.length}'),
                  ),
                  SizedBox(
                    width: 150,
                    child: StatTile(label: l10n.commonEmployees, value: '${_sum('employees_total')}'),
                  ),
                  SizedBox(
                    width: 150,
                    child: StatTile(label: l10n.commonProjects, value: '${_sum('projects_total')}'),
                  ),
                  SizedBox(
                    width: 150,
                    child: StatTile(label: l10n.commonAgentTokens, value: '${_sum('agent_tokens_used')}'),
                  ),
                ],
              ),
            ),
          ],
          Expanded(
            child: AppEntityCollection(
              loading: _loading,
              rows: rows,
              columns: [
                AppEntityColumn(id: 'name', label: l10n.commonCompany),
                AppEntityColumn(id: 'employees', label: l10n.commonEmployees),
                AppEntityColumn(id: 'cabinets', label: l10n.commonCabinets),
                AppEntityColumn(id: 'projects', label: l10n.commonProjects),
                AppEntityColumn(id: 'tokens', label: l10n.adminTokens),
              ],
              onOpen: _openCompany,
              empty: EmptyState(
                title: l10n.adminNoCompaniesYet,
                subtitle: l10n.adminCreateCompanyToSeeMetrics,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
