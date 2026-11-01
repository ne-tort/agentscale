import 'package:flutter/material.dart';

import 'package:prodavan/core/containers/container_runtime_presenter.dart';
import 'package:prodavan/core/jobs/app_job_store.dart';
import 'package:prodavan/core/jobs/project_lifecycle_jobs.dart';
import 'package:prodavan/core/jobs/project_wake_flow.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/widgets/app_confirm_page.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_snack_bar.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/features/employee/project_schedules_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Pause/resume, sync, and agent reset for launched projects.
class ProjectManagementPage extends StatefulWidget {
  const ProjectManagementPage({
    super.key,
    required this.projectId,
    required this.projectName,
  });

  final String projectId;
  final String projectName;

  @override
  State<ProjectManagementPage> createState() => _ProjectManagementPageState();
}

class _ProjectManagementPageState extends State<ProjectManagementPage> {
  bool _busy = false;
  bool _pausing = false;
  bool _resuming = false;
  bool _syncing = false;
  bool _loading = true;
  AppJobStatus? _seenJobStatus;
  String? _status;
  Map<String, dynamic>? _runtime;
  String? _workspaceOutdatedAt;

  @override
  void initState() {
    super.initState();
    appJobStore.addListener(_onJobs);
    _bootstrapJobsThenLoad();
  }

  @override
  void dispose() {
    appJobStore.removeListener(_onJobs);
    super.dispose();
  }

  AppJob? get _job {
    final running = appJobStore.lifecycleFor(widget.projectId);
    if (running != null) return running;
    AppJob? latest;
    for (final kind in AppJobKinds.lifecycle) {
      final candidate = appJobStore.bySubject(
        kind: kind,
        subjectId: widget.projectId,
      );
      if (candidate != null &&
          (latest == null || candidate.updatedAt.isAfter(latest.updatedAt))) {
        latest = candidate;
      }
    }
    return latest;
  }

  bool get _jobBusy {
    final job = appJobStore.lifecycleFor(widget.projectId);
    return job != null && !job.isExpired;
  }

  void _onJobs() {
    if (!mounted) return;
    final status = _job?.status;
    final finished = _seenJobStatus == AppJobStatus.running &&
        (status == AppJobStatus.succeeded || status == AppJobStatus.failed);
    _seenJobStatus = status;
    setState(() {});
    if (finished) _load();
  }

  Future<void> _bootstrapJobsThenLoad() async {
    await appJobStore.ensureHydrated();
    if (!mounted) return;
    final job = _job;
    if (job != null && job.status == AppJobStatus.running && !job.isExpired) {
      unawaited(_reattachPersistedJob());
    }
    if (mounted) await _load();
  }

  Future<void> _reattachPersistedJob() async {
    try {
      await resumePersistedLifecycleJob(
        store: appJobStore,
        api: workContext.api,
        projectId: widget.projectId,
        l10n: AppLocalizations.of(context),
      );
    } catch (_) {
      // The persisted job carries the user-visible failure state.
    }
  }

  Future<void> _load() async {
    try {
      final project = await workContext.api.getProject(widget.projectId);
      if (!mounted) return;
      setState(() {
        _status = project['status'] as String?;
        _runtime = project['runtime'] is Map
            ? Map<String, dynamic>.from(project['runtime'] as Map)
            : null;
        _workspaceOutdatedAt = project['workspace_outdated_at'] as String?;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      AppErrors.showSnack(context, e);
    }
  }

  Future<void> _pause() async {
    final l10n = AppLocalizations.of(context);
    final ok = await AppConfirmPage.push(
      context,
      title: l10n.projectPauseProject,
      message: l10n.projectPauseConfirmMessage,
      confirmLabel: l10n.projectPauseProject,
      severity: AppStatusSeverity.warning,
    );
    if (!ok || !mounted) return;
    // Pause via lifecycle job + poll (waitFor suspended): the button flips
    // only once the sandbox actually sleeps, not on the bare API call.
    setState(() => _pausing = true);
    try {
      await runProjectWakeFlow(
        context: context,
        action: ProjectWakeAction.pause,
        store: appJobStore,
        api: workContext.api,
        projectId: widget.projectId,
        onSettled: _load,
      );
    } finally {
      if (mounted) setState(() => _pausing = false);
    }
  }

  Future<void> _resume() async {
    final l10n = AppLocalizations.of(context);
    AppSnackBar.info(context, l10n.projectResumeStartingSnack);
    setState(() => _resuming = true);
    try {
      await runProjectWakeFlow(
        context: context,
        action: ProjectWakeAction.resume,
        store: appJobStore,
        api: workContext.api,
        projectId: widget.projectId,
        onSettled: _load,
      );
    } finally {
      if (mounted) setState(() => _resuming = false);
    }
  }

  Future<void> _syncProject() async {
    setState(() => _syncing = true);
    try {
      await workContext.api.syncProject(widget.projectId);
      if (!mounted) return;
      AppSnackBar.success(context, AppLocalizations.of(context).projectUpdateSuccess);
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    } finally {
      if (mounted) setState(() => _syncing = false);
    }
  }

  String _scheduleLabel(BuildContext context) =>
      Localizations.localeOf(context).languageCode == 'ru' ? 'Расписания' : 'Schedules';

  void _openSchedules() {
    Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => ProjectSchedulesPage(
          projectId: widget.projectId,
        ),
      ),
    );
  }

  Future<void> _resetAgent() async {
    final l10n = AppLocalizations.of(context);
    final ok = await AppConfirmPage.push(
      context,
      title: l10n.projectResetAgent,
      message: l10n.projectResetWarning,
      confirmLabel: l10n.projectResetAgent,
      severity: AppStatusSeverity.critical,
    );
    if (!ok || !mounted) return;
    setState(() => _busy = true);
    try {
      await workContext.api.resetProjectAgent(widget.projectId);
      if (!mounted) return;
      AppSnackBar.success(context, l10n.projectResetSuccess);
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final warning = context.appColors.warning;
    final paused = containerIsPaused({
      'status': _status,
      'runtime': _runtime,
      'observed_state': _runtime?['observed_state'],
    });
    final workspaceOutdated = _workspaceOutdatedAt != null && _workspaceOutdatedAt!.isNotEmpty;
    final job = _job;
    final showProgress = _jobBusy && job != null;
    final actionBusy = showProgress || _pausing || _resuming || _syncing || _busy;
    final enabled = !actionBusy && !_loading;

    return AppScaffold(
      title: Text(l10n.projectProjectManagement),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: EdgeInsets.all(AppSpacing.md),
              children: [
                if (workspaceOutdated && !paused)
                  AppStatusBanner(
                    message: l10n.projectWorkspaceOutdated,
                    severity: AppStatusSeverity.warning,
                  ),
                if (showProgress)
                  AppNavPreference(
                    title: job.title.isNotEmpty ? job.title : l10n.projectResumeInProgress,
                    icon: Icons.hourglass_top_outlined,
                    accentColor: warning,
                    loading: true,
                    loadingLabel: job.subtitle.isNotEmpty
                        ? job.subtitle
                        : l10n.projectResumeInProgress,
                    onTap: () {},
                  )
                else if (paused) ...[
                  AppNavPreference(
                    title: l10n.projectResumeProject,
                    icon: Icons.play_arrow_outlined,
                    accentColor: warning,
                    enabled: enabled,
                    loading: _resuming,
                    loadingLabel: l10n.projectResumeInProgress,
                    onTap: _resume,
                  ),
                  AppNavPreference(
                    title: l10n.projectResetAgent,
                    icon: Icons.restart_alt_outlined,
                    enabled: enabled,
                    onTap: _resetAgent,
                  ),
                ] else ...[
                  AppNavPreference(
                    title: l10n.projectPauseProject,
                    icon: Icons.pause_outlined,
                    accentColor: warning,
                    enabled: enabled,
                    loading: _pausing,
                    loadingLabel: l10n.projectPauseProject,
                    onTap: _pause,
                  ),
                  AppNavPreference(
                    title: l10n.projectUpdateProject,
                    icon: Icons.sync_outlined,
                    enabled: enabled,
                    loading: _syncing,
                    loadingLabel: l10n.projectUpdateProject,
                    onTap: _syncProject,
                  ),
                  AppNavPreference(
                    title: l10n.projectResetAgent,
                    icon: Icons.restart_alt_outlined,
                    enabled: enabled,
                    onTap: _resetAgent,
                  ),
                ],
                AppNavPreference(
                  title: _scheduleLabel(context),
                  icon: Icons.schedule_outlined,
                  enabled: enabled,
                  onTap: _openSchedules,
                ),
              ],
            ),
    );
  }
}
