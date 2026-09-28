import 'package:flutter/material.dart';

import 'package:prodavan/core/jobs/app_job_store.dart';
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
  String? _status;
  String? _workspaceOutdatedAt;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final project = await workContext.api.getProject(widget.projectId);
      if (!mounted) return;
      setState(() {
        _status = project['status'] as String?;
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

  Future<void> _resetAgent() async {
    final l10n = AppLocalizations.of(context);
    final ok = await AppConfirmPage.push(
      context,
      title: l10n.projectResetAgent,
      message: l10n.projectResetAgent,
      confirmLabel: l10n.projectResetAgent,
      severity: AppStatusSeverity.warning,
    );
    if (!ok) return;
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
    final paused = _status == 'paused';
    final workspaceOutdated = _workspaceOutdatedAt != null && _workspaceOutdatedAt!.isNotEmpty;
    final actionBusy = _pausing || _resuming || _syncing || _busy;
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
                if (paused)
                  AppNavPreference(
                    title: l10n.projectResumeProject,
                    icon: Icons.play_arrow_outlined,
                    accentColor: warning,
                    enabled: enabled,
                    loading: _resuming,
                    loadingLabel: l10n.projectResumeInProgress,
                    onTap: _resume,
                  )
                else ...[
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
              ],
            ),
    );
  }
}
