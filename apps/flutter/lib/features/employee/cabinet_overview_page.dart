import 'package:flutter/material.dart';

import 'package:prodavan/core/refresh/app_auto_refresh.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/cabinet_metrics_wrap.dart';

/// Cabinet overview — scoped org metrics (StatTile wrap).
class CabinetOverviewPage extends StatefulWidget {
  const CabinetOverviewPage({
    super.key,
    required this.cabinetId,
    required this.cabinetName,
  });

  final String cabinetId;
  final String cabinetName;

  @override
  State<CabinetOverviewPage> createState() => _CabinetOverviewPageState();
}

class _CabinetOverviewPageState extends State<CabinetOverviewPage> {
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
      final metrics = await workContext.api.getCabinetMetrics(widget.cabinetId);
      if (!mounted) return;
      if (silent && appRefreshDataEquals(_metrics, metrics) && !_loading) {
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
              padding: EdgeInsets.all(AppSpacing.md),
              children: [
                if (_error != null)
                  Padding(
                    padding: EdgeInsets.only(bottom: AppSpacing.sm),
                    child: AppStatusBanner(
                      severity: AppStatusSeverity.error,
                      message: AppErrors.localize(context, _error!),
                    ),
                  ),
                CabinetMetricsWrap(metrics: _metrics, showLastActivity: false),
              ],
            ),
    );
  }
}
