import 'dart:async';

import 'package:flutter/material.dart';

import 'package:prodavan/core/containers/container_runtime_presenter.dart';
import 'package:prodavan/core/jobs/app_job_store.dart';
import 'package:prodavan/core/jobs/project_lifecycle_jobs.dart';
import 'package:prodavan/core/jobs/project_wake_flow.dart';
import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/refresh/app_auto_refresh.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/container_metrics_wrap.dart';
import 'package:prodavan/features/containers/container_workspace_api.dart';
import 'package:prodavan/features/containers/container_workspace_files_page.dart';
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
  Map<String, dynamic>? _projectMetrics;

  AppJob? get _job => appJobStore.lifecycleFor(widget.projectId);

  bool get _busy {
    final job = _job;
    return job != null && job.status == AppJobStatus.running && !job.isExpired;
  }

  @override
  void initState() {
    super.initState();
    appJobStore.addListener(_onJobs);
    _autoRefresh = AppAutoRefreshBinder(
      onTick: () => _load(silent: true),
      isActive: () => appAutoRefreshIsActive(context),
    )..attach();
    unawaited(_bootstrap());
  }

  Future<void> _bootstrap() async {
    await appJobStore.ensureHydrated();
    if (!mounted) return;
    final job = appJobStore.lifecycleFor(widget.projectId);
    if (job != null && job.status == AppJobStatus.running && !job.isExpired) {
      unawaited(
        resumePersistedLifecycleJob(
          store: appJobStore,
          api: workContext.api,
          projectId: widget.projectId,
          l10n: AppLocalizations.of(context),
        ),
      );
    }
    await _load();
  }

  @override
  void dispose() {
    appJobStore.removeListener(_onJobs);
    _autoRefresh.dispose();
    super.dispose();
  }

  void _onJobs() {
    if (mounted) setState(() {});
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
    await runProjectWakeFlow(
      context: context,
      action: ProjectWakeAction.reload,
      store: appJobStore,
      api: workContext.api,
      projectId: widget.projectId,
      successSnack: (l10n) => l10n.projectReloadSuccess,
      onSettled: _load,
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final warning = context.appColors.warning;
    final job = _job;
    final showProgress = _busy && job != null;

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
                if (containerRuntimeHealthy(_container))
                  AppNavPreference(
                    title: l10n.projectWorkspaceFiles,
                    icon: Icons.folder_outlined,
                    enabled: !_busy,
                    onTap: () {
                      Navigator.of(context).push(
                        MaterialPageRoute<void>(
                          builder: (context) => ContainerWorkspaceFilesPage(
                            title: l10n.projectWorkspaceFiles,
                            api: EmployeeContainerWorkspaceApi(
                              workContext.api,
                              widget.projectId,
                            ),
                          ),
                        ),
                      );
                    },
                  ),
                if (showProgress)
                  AppNavPreference(
                    title: job.title.isNotEmpty
                        ? job.title
                        : l10n.projectReloadInProgress,
                    icon: Icons.refresh_outlined,
                    accentColor: warning,
                    loading: true,
                    loadingLabel: job.subtitle.isNotEmpty
                        ? job.subtitle
                        : l10n.projectLaunchStartingSnack,
                    onTap: () {},
                  )
                else
                  AppNavPreference(
                    title: l10n.projectReload,
                    icon: Icons.refresh_outlined,
                    accentColor: warning,
                    onTap: _reload,
                  ),
              ],
            ),
    );
  }
}
