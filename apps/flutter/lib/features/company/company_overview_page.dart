import 'package:flutter/material.dart';

import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_section_header.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';
import 'package:prodavan/core/widgets/stat_tile.dart';

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
    final v = _metrics?[key];
    if (v == null) return '—';
    return '$v';
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }
    return ListView(
      padding: const EdgeInsets.all(AppSpacing.md),
      children: [
        if (_error != null) InlineErrorBanner(message: _error!),
        const AppSectionHeader(title: 'Org metrics'),
        Wrap(
          spacing: AppSpacing.sm,
          runSpacing: AppSpacing.sm,
          children: [
            SizedBox(width: 160, child: StatTile(label: 'Employees', value: _metric('employees_total'))),
            SizedBox(width: 160, child: StatTile(label: 'Active', value: _metric('employees_active'))),
            SizedBox(
              width: 160,
              child: StatTile(
                label: 'Cabinets',
                value: '${_metric('active_cabinets')} / ${_metric('cabinets_quota')}',
              ),
            ),
            SizedBox(width: 160, child: StatTile(label: 'Projects', value: _metric('projects_total'))),
            SizedBox(width: 160, child: StatTile(label: 'Agent tokens', value: _metric('agent_tokens_used'))),
            SizedBox(width: 160, child: StatTile(label: 'Storage (bytes)', value: _metric('storage_bytes'))),
            SizedBox(
              width: 200,
              child: StatTile(label: 'Last activity', value: _metricOrDash('last_activity_at')),
            ),
          ],
        ),
      ],
    );
  }
}
