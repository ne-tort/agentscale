import 'package:flutter/material.dart';

import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
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
            subtitle: next?.toString() ?? l10n.adminAlertKeysRenewingSoon('$expiring'),
          ),
        );
      }
      if (c['high_agent_usage'] == true) {
        alerts.add(
          _OverviewAlert(
            kind: _AlertKind.highUsage,
            companyId: id,
            companyName: name,
            subtitle: l10n.adminAlertTokensAboveThreshold('${c['agent_tokens_used'] ?? '?'}'),
          ),
        );
      }
      if (c['subscription_expired'] == true) {
        alerts.add(
          _OverviewAlert(
            kind: _AlertKind.subscriptionExpired,
            companyId: id,
            companyName: name,
            subtitle: l10n.adminAlertSubscriptionEnded,
          ),
        );
      } else if (c['subscription_expiring'] == true) {
        alerts.add(
          _OverviewAlert(
            kind: _AlertKind.subscriptionExpiring,
            companyId: id,
            companyName: name,
            subtitle: l10n.adminAlertSubscriptionEndsSoonAt('${c['subscription_ends_at'] ?? l10n.commonEmDash}'),
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
    final colors = context.appColors;
    return AppScaffold(
      title: Text(l10n.commonOverview),
      actions: [
        IconButton(onPressed: _reload, icon: const Icon(Icons.refresh)),
      ],
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.all(AppSpacing.md),
              children: [
                if (_error != null) InlineErrorBanner(message: _error!),
                if (_alerts.isNotEmpty) ...[
                  Text(l10n.adminAlerts, style: Theme.of(context).textTheme.titleMedium),
                  const SizedBox(height: AppSpacing.sm),
                  for (final a in _alerts)
                    ListTile(
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
                      onTap: () => _openCompany(a.companyId, a.companyName),
                    ),
                  const SizedBox(height: AppSpacing.lg),
                ],
                if (_items.isNotEmpty) ...[
                  Text(l10n.adminMetrics, style: Theme.of(context).textTheme.titleMedium),
                  const SizedBox(height: AppSpacing.sm),
                  Wrap(
                    spacing: AppSpacing.sm,
                    runSpacing: AppSpacing.sm,
                    children: [
                      StatTile(label: l10n.navCompanies, value: '${_items.length}'),
                      StatTile(label: l10n.commonEmployees, value: '${_sum('employees_total')}'),
                      StatTile(label: l10n.commonProjects, value: '${_sum('projects_total')}'),
                      StatTile(label: l10n.commonAgentTokens, value: '${_sum('agent_tokens_used')}'),
                    ],
                  ),
                ] else
                  EmptyPlaceholder(
                    title: l10n.adminCreateCompanyToSeeMetrics,
                    icon: Icons.analytics_outlined,
                    fillViewport: false,
                  ),
              ],
            ),
    );
  }
}
