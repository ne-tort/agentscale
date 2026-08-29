import 'package:flutter/material.dart';

import 'package:prodavan/core/containers/container_runtime_presenter.dart';
import 'package:prodavan/core/format/storage_format.dart';
import 'package:prodavan/core/refresh/app_auto_refresh.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/stat_tile.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Project container — pod runtime and k8s metrics (employee).
class ProjectContainerPage extends StatefulWidget {
  const ProjectContainerPage({
    super.key,
    required this.projectId,
    required this.projectName,
  });

  final String projectId;
  final String projectName;

  @override
  State<ProjectContainerPage> createState() => _ProjectContainerPageState();
}

class _ProjectContainerPageState extends State<ProjectContainerPage> {
  late final AppAutoRefreshBinder _autoRefresh;
  bool _loading = true;
  Map<String, dynamic>? _container;
  Map<String, dynamic>? _metrics;

  @override
  void initState() {
    super.initState();
    _autoRefresh = AppAutoRefreshBinder(
      onTick: () => _load(silent: true),
      isActive: () => appAutoRefreshIsActive(context),
    )..attach();
    _load();
  }

  @override
  void dispose() {
    _autoRefresh.dispose();
    super.dispose();
  }

  Future<void> _load({bool silent = false}) async {
    if (!silent && mounted) setState(() => _loading = true);
    try {
      final container = await workContext.api.getProjectContainer(widget.projectId);
      Map<String, dynamic>? metrics;
      try {
        metrics = await workContext.api.getProjectContainerMetrics(widget.projectId);
      } catch (_) {}
      if (!mounted) return;
      if (silent &&
          appRefreshDataEquals(_container, container) &&
          appRefreshDataEquals(_metrics, metrics) &&
          !_loading) {
        return;
      }
      setState(() {
        _container = container;
        _metrics = metrics;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      if (silent) return;
      setState(() => _loading = false);
      AppErrors.showSnack(context, e);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final latest = _metrics?['latest'];
    final latestMap = latest is Map ? Map<String, dynamic>.from(latest) : null;
    final cpu = latestMap?['cpu_millicores'];
    final mem = latestMap?['memory_bytes'];

    return AppScaffold(
      title: Text(widget.projectName),
      body: _loading && _container == null
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: EdgeInsets.all(AppSpacing.md),
              children: [
                Text(
                  formatContainerRuntimeDetail(_container, l10n),
                  style: Theme.of(context).textTheme.bodyMedium,
                ),
                SizedBox(height: AppSpacing.md),
                Wrap(
                  spacing: AppSpacing.sm,
                  runSpacing: AppSpacing.sm,
                  children: [
                    if (cpu is num)
                      StatTile(
                        label: l10n.adminContainerMetricsCpu,
                        value: '${cpu.round()}m',
                      ),
                    if (mem is num)
                      StatTile(
                        label: l10n.adminContainerMetricsMemory,
                        value: formatStorageGb(mem),
                      ),
                  ],
                ),
              ],
            ),
    );
  }
}
