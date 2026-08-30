import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/refresh/app_auto_refresh.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_snack_bar.dart';
import 'package:prodavan/core/widgets/container_metrics_wrap.dart';
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
  bool _reloading = false;
  Map<String, dynamic>? _container;
  Map<String, dynamic>? _metrics;
  Map<String, dynamic>? _projectMetrics;

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
      Map<String, dynamic>? projectMetrics;
      try {
        metrics = await workContext.api.getProjectContainerMetrics(widget.projectId);
      } catch (_) {}
      try {
        projectMetrics = await workContext.api.getProjectMetrics(widget.projectId);
      } catch (_) {}
      if (!mounted) return;
      if (silent &&
          appRefreshDataEquals(_container, container) &&
          appRefreshDataEquals(_metrics, metrics) &&
          appRefreshDataEquals(_projectMetrics, projectMetrics) &&
          !_loading) {
        return;
      }
      setState(() {
        _container = container;
        _metrics = metrics;
        _projectMetrics = projectMetrics;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      if (silent) return;
      setState(() => _loading = false);
      AppErrors.showSnack(context, e);
    }
  }

  Future<void> _reload() async {
    setState(() => _reloading = true);
    try {
      await workContext.api.reloadProject(widget.projectId);
      if (!mounted) return;
      AppSnackBar.success(context, AppLocalizations.of(context).projectReloadSuccess);
      await _load();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    } finally {
      if (mounted) setState(() => _reloading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final warning = context.appColors.warning;

    return AppScaffold(
      title: Text(widget.projectName),
      body: _loading && _container == null
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: EdgeInsets.all(AppSpacing.md),
              children: [
                ContainerMetricsWrap(
                  container: _container,
                  runtimeMetrics: _metrics,
                  projectMetrics: _projectMetrics,
                ),
                AppNavPreference(
                  title: l10n.projectReload,
                  icon: Icons.refresh_outlined,
                  accentColor: warning,
                  enabled: !_reloading,
                  loading: _reloading,
                  loadingLabel: l10n.projectReload,
                  onTap: _reload,
                ),
              ],
            ),
    );
  }
}
