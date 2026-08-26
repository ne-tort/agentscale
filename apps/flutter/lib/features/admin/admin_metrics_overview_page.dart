import 'package:flutter/material.dart';

import 'package:prodavan/core/refresh/app_auto_refresh.dart';
import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/core/widgets/stat_tile.dart';
import 'package:prodavan/features/admin/company/admin_company_detail_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

enum _AlertKind { noKeys, keyExpiring, highUsage, subscriptionExpiring, subscriptionExpired }

class _OverviewAlert {
  const _OverviewAlert({
    required this.kind,
    required this.companyId,
    required this.companyName,
    required this.tag,
  });

  final _AlertKind kind;
  final String companyId;
  final String companyName;
  final String tag;
}

/// Platform-wide metrics overview — «Сводка» tab (L04 / metrics.md).
class AdminMetricsOverviewPage extends StatefulWidget {
  const AdminMetricsOverviewPage({super.key, this.embedded = false});

  final bool embedded;

  @override
  State<AdminMetricsOverviewPage> createState() => _AdminMetricsOverviewPageState();
}

class _AdminMetricsOverviewPageState extends State<AdminMetricsOverviewPage> {
  late final AppAutoRefreshBinder _autoRefresh;
  bool _loading = true;
  List<Map<String, dynamic>> _items = const [];
  List<_OverviewAlert> _alerts = const [];

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
      setState(() => _loading = true);
    }
    try {
      final items = await adminContext.api.listCompaniesMetrics();
      if (!mounted) return;
      if (silent && appRefreshDataEquals(_items, items) && !_loading) return;
      setState(() {
        _items = items;
        _alerts = _buildAlerts(items);
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      if (silent) return;
      setState(() => _loading = false);
      AppErrors.showSnack(context, e);
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
            tag: l10n.adminAlertTagNoKeys,
          ),
        );
      }
      if (expiring > 0) {
        alerts.add(
          _OverviewAlert(
            kind: _AlertKind.keyExpiring,
            companyId: id,
            companyName: name,
            tag: l10n.adminAlertTagKeyRenewal(expiring),
          ),
        );
      }
      if (c['high_agent_usage'] == true) {
        alerts.add(
          _OverviewAlert(
            kind: _AlertKind.highUsage,
            companyId: id,
            companyName: name,
            tag: l10n.adminAlertTagHighUsage,
          ),
        );
      }
      if (c['subscription_expired'] == true) {
        alerts.add(
          _OverviewAlert(
            kind: _AlertKind.subscriptionExpired,
            companyId: id,
            companyName: name,
            tag: l10n.adminAlertTagSubExpired,
          ),
        );
      } else if (c['subscription_expiring'] == true) {
        alerts.add(
          _OverviewAlert(
            kind: _AlertKind.subscriptionExpiring,
            companyId: id,
            companyName: name,
            tag: l10n.adminAlertTagSubExpiring,
          ),
        );
      }
    }
    return alerts;
  }

  String _companyId(Map<String, dynamic> item) =>
      item['company_id'] as String? ?? item['id'] as String? ?? '';

  int _asInt(dynamic v) {
    if (v is int) return v;
    if (v is num) return v.toInt();
    return int.tryParse('$v') ?? 0;
  }

  int _sum(String key) {
    var total = 0;
    for (final item in _items) {
      total += _asInt(item[key]);
    }
    return total;
  }

  void _openCompany(String companyId, String companyName) {
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => AdminCompanyDetailPage(companyId: companyId, companyName: companyName),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return AppScaffold(
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Padding(
                  padding: const EdgeInsets.fromLTRB(
                    AppSpacing.md,
                    AppSpacing.md,
                    AppSpacing.md,
                    AppSpacing.sm,
                  ),
                  child: _items.isNotEmpty
                      ? Wrap(
                          spacing: AppSpacing.sm,
                          runSpacing: AppSpacing.sm,
                          children: [
                            StatTile(label: l10n.navCompanies, value: '${_items.length}'),
                            StatTile(label: l10n.commonEmployees, value: '${_sum('employees_total')}'),
                            StatTile(label: l10n.commonProjects, value: '${_sum('projects_total')}'),
                            StatTile(label: l10n.commonAgentTokens, value: '${_sum('agent_tokens_used')}'),
                          ],
                        )
                      : EmptyPlaceholder(
                          title: l10n.adminCreateCompanyToSeeMetrics,
                          icon: Icons.analytics_outlined,
                          fillViewport: false,
                        ),
                ),
                if (_alerts.isNotEmpty) ...[
                  Padding(
                    padding: const EdgeInsets.fromLTRB(
                      AppSpacing.md,
                      AppSpacing.sm,
                      AppSpacing.md,
                      AppSpacing.xs,
                    ),
                    child: Text(
                      l10n.adminAlerts,
                      style: Theme.of(context).textTheme.titleSmall,
                    ),
                  ),
                  Expanded(
                    child: AppEntityCollection(
                      mode: AppEntityCollectionMode.table,
                      primaryColumnLabel: l10n.navCompanies,
                      columns: [
                        AppEntityColumn(
                          id: 'tag',
                          label: l10n.adminAlertTag,
                          align: AppEntityColumnAlign.end,
                        ),
                      ],
                      rows: [
                        for (final a in _alerts)
                          AppEntityRow(
                            id: '${a.companyId}:${a.kind.name}',
                            title: a.companyName,
                            cells: {'tag': a.tag},
                          ),
                      ],
                      onOpen: (row) {
                        final alert = _alerts.firstWhere(
                          (a) => '${a.companyId}:${a.kind.name}' == row.id,
                        );
                        _openCompany(alert.companyId, alert.companyName);
                      },
                    ),
                  ),
                ] else
                  const Spacer(),
              ],
            ),
    );
  }
}
