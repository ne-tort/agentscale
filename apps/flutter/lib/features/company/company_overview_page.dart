import 'package:flutter/material.dart';

import 'package:prodavan/core/refresh/app_auto_refresh.dart';
import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/core/widgets/company_metrics_wrap.dart';

/// Company overview — org metrics (L04).
class CompanyOverviewPage extends StatefulWidget {
  const CompanyOverviewPage({super.key, required this.companyId});

  final String companyId;

  @override
  State<CompanyOverviewPage> createState() => _CompanyOverviewPageState();
}

class _CompanyOverviewPageState extends State<CompanyOverviewPage> {
  late final AppAutoRefreshBinder _autoRefresh;
  bool _loading = true;
  Object? _error;
  Map<String, dynamic>? _metrics;

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
      final summary = await companyContext.api.getSummary(widget.companyId);
      if (!mounted) return;
      final metrics = summary['metrics'] as Map<String, dynamic>?;
      if (silent &&
          appRefreshDataEquals(_metrics, metrics) &&
          !_loading) {
        return;
      }
      setState(() {
        _metrics = metrics;
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
    return AppScaffold(
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
              children: [
                if (_error != null)
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md),
                    child: AppStatusBanner(
                      severity: AppStatusSeverity.error,
                      message: AppErrors.localize(context, _error!),
                    ),
                  ),
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md),
                  child: CompanyMetricsWrap(
                    metrics: _metrics,
                    showActiveEmployees: false,
                    showLastActivity: false,
                  ),
                ),
              ],
            ),
    );
  }
}
