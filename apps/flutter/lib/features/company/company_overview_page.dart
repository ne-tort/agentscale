import 'package:flutter/material.dart';

import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_section_header.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';
import 'package:prodavan/core/widgets/stat_tile.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Company overview — org metrics read-only (L04).
class CompanyOverviewPage extends StatefulWidget {
  const CompanyOverviewPage({super.key, required this.companyId});

  final String companyId;

  @override
  State<CompanyOverviewPage> createState() => _CompanyOverviewPageState();
}

class _CompanyOverviewPageState extends State<CompanyOverviewPage> {
  bool _loading = true;
  String? _error;
  Map<String, dynamic>? _metrics;

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
      final summary = await companyContext.api.getSummary(widget.companyId);
      if (!mounted) return;
      setState(() {
        _metrics = summary['metrics'] as Map<String, dynamic>?;
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

  String _metric(String key) => '${_metrics?[key] ?? 0}';

  String _metricOrDash(String key) {
    final l10n = AppLocalizations.of(context);
    final v = _metrics?[key];
    if (v == null) return l10n.commonEmDash;
    return '$v';
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }
    return ListView(
      padding: EdgeInsets.all(AppSpacing.md),
      children: [
        if (_error != null) InlineErrorBanner(message: _error!),
        AppSectionHeader(title: l10n.companyOrgMetrics),
        Wrap(
          spacing: AppSpacing.sm,
          runSpacing: AppSpacing.sm,
          children: [
            SizedBox(width: 160, child: StatTile(label: l10n.commonEmployees, value: _metric('employees_total'))),
            SizedBox(width: 160, child: StatTile(label: l10n.companyActive, value: _metric('employees_active'))),
            SizedBox(
              width: 160,
              child: StatTile(
                label: l10n.commonCabinets,
                value: '${_metric('active_cabinets')} / ${_metric('cabinets_quota')}',
              ),
            ),
            SizedBox(width: 160, child: StatTile(label: l10n.commonProjects, value: _metric('projects_total'))),
            SizedBox(width: 160, child: StatTile(label: l10n.commonAgentTokens, value: _metric('agent_tokens_used'))),
            SizedBox(width: 160, child: StatTile(label: l10n.commonStorageBytes, value: _metric('storage_bytes'))),
            SizedBox(
              width: 200,
              child: StatTile(label: l10n.commonLastActivity, value: _metricOrDash('last_activity_at')),
            ),
          ],
        ),
      ],
    );
  }
}
