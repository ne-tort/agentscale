import 'package:flutter/material.dart';

import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_section_header.dart';
import 'package:prodavan/core/widgets/company_metrics_wrap.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';
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

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final title = companyContext.companyName ?? l10n.navOverview;
    return AppScaffold(
      title: Text(title),
      actions: [
        IconButton(onPressed: _loading ? null : _reload, icon: const Icon(Icons.refresh)),
      ],
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.all(AppSpacing.md),
              children: [
                if (_error != null) InlineErrorBanner(message: _error!),
                AppSectionHeader(title: l10n.companyOrgMetrics),
                CompanyMetricsWrap(metrics: _metrics),
              ],
            ),
    );
  }
}
