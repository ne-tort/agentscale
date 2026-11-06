import 'dart:async';

import 'package:flutter/material.dart';

import 'package:prodavan/core/containers/container_runtime_presenter.dart';
import 'package:prodavan/core/jobs/app_job_store.dart';
import 'package:prodavan/core/jobs/project_lifecycle_jobs.dart';
import 'package:prodavan/core/jobs/project_wake_flow.dart';
import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_trailing_chevron.dart';
import 'package:prodavan/core/widgets/project_metrics_wrap.dart';
import 'package:prodavan/features/employee/project_about_page.dart';
import 'package:prodavan/features/employee/project_ai_key_select_page.dart';
import 'package:prodavan/features/employee/project_ai_provider_binding.dart';
import 'package:prodavan/features/employee/project_container_page.dart';
import 'package:prodavan/features/employee/project_dialogs_page.dart';
import 'package:prodavan/features/employee/project_management_page.dart';
import 'package:prodavan/features/employee/project_modules_list_page.dart';
import 'package:prodavan/features/employee/project_schedules_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Project settings — name, about, launch/pause/resume, AI provider, modules nav.
class CabinetProjectSettingsPage extends StatefulWidget {
  const CabinetProjectSettingsPage({
    super.key,
    required this.cabinetId,
    required this.projectId,
  });

  final String cabinetId;
  final String projectId;

  @override
  State<CabinetProjectSettingsPage> createState() => _CabinetProjectSettingsPageState();
}

class _CabinetProjectSettingsPageState extends State<CabinetProjectSettingsPage> {
  String _name = '';
  String? _resolvedKeyId;
  String? _status;
  Map<String, dynamic>? _runtime;
  bool _hasPod = false;
  List<Map<String, dynamic>> _availableKeys = const [];
  bool _availableKeysLoaded = false;
  Map<String, dynamic>? _metrics;
  bool _loading = true;
  AppJobStatus? _seenJobStatus;
  bool _resumeAttached = false;

  AppJob? get _job => appJobStore.lifecycleFor(widget.projectId);

  bool get _busy {
    final job = _job;
    return job != null && job.status == AppJobStatus.running && !job.isExpired;
  }

  @override
  void initState() {
    super.initState();
    appJobStore.addListener(_onJobs);
    unawaited(
      workContext.selectProject(
        cabinetId: widget.cabinetId,
        projectId: widget.projectId,
      ),
    );
    unawaited(_bootstrapJobsThenLoad());
  }

  Future<void> _bootstrapJobsThenLoad() async {
    await appJobStore.ensureHydrated();
    if (!mounted) return;
    await _attachPersistedJob();
    if (!mounted) return;
    await _load();
  }

  Future<void> _attachPersistedJob() async {
    if (_resumeAttached) return;
    final job = appJobStore.lifecycleFor(widget.projectId);
    if (job == null || job.status != AppJobStatus.running || job.isExpired) {
      return;
    }
    _resumeAttached = true;
    final l10n = AppLocalizations.of(context);
    try {
      await resumePersistedLifecycleJob(
        store: appJobStore,
        api: workContext.api,
        projectId: widget.projectId,
        l10n: l10n,
      );
    } catch (_) {
      // surfaced via job status / next _load
    } finally {
      if (mounted) await _load();
    }
  }

  @override
  void dispose() {
    appJobStore.removeListener(_onJobs);
    super.dispose();
  }

  void _onJobs() {
    if (!mounted) return;
    final job = _job;
    final status = job?.status;
    final finished = _seenJobStatus == AppJobStatus.running &&
        (status == AppJobStatus.succeeded || status == AppJobStatus.failed);
    _seenJobStatus = status;
    setState(() {});
    if (finished) {
      unawaited(_load());
    }
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final project = await workContext.api.getProject(widget.projectId);
      List<Map<String, dynamic>> keys = const [];
      var keysLoaded = false;
      try {
        keys = await workContext.api.listProjectAiKeys(widget.projectId);
        keysLoaded = true;
      } catch (_) {}
      Map<String, dynamic>? metrics;
      try {
        metrics = await workContext.api.getProjectMetrics(widget.projectId);
      } catch (_) {}
      if (!mounted) return;

      var resolvedKeyId = project['resolved_ai_key_id'] as String?;
      if (resolvedKeyId == null && keysLoaded && keys.length == 1) {
        final onlyId = keys.first['id'] as String?;
        if (onlyId != null) {
          await workContext.api.patchProject(
            projectId: widget.projectId,
            resolvedAiKeyId: onlyId,
          );
          resolvedKeyId = onlyId;
        }
      }

      setState(() {
        _name = project['name'] as String? ?? '';
        _resolvedKeyId = resolvedKeyId;
        _status = project['status'] as String? ?? 'draft';
        _runtime = project['runtime'] is Map
            ? Map<String, dynamic>.from(project['runtime'] as Map)
            : null;
        _hasPod = _runtime != null || projectHasLivePod(project);
        _availableKeys = keys;
        _availableKeysLoaded = keysLoaded;
        _metrics = metrics;
        _loading = false;
      });

      final inFlight = containerIsInFlight({
        'runtime': _runtime,
        'observed_state': _runtime?['observed_state'],
      });
      if (inFlight &&
          !_busy &&
          !appJobStore.isLifecycleActive(widget.projectId)) {
        unawaited(_joinInFlight());
      }
    } catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      AppErrors.showSnack(context, e);
    }
  }

  // A stored id is not enough: the API only lists keys that are active,
  // project-allowed, and runtime-capable. Launch must stay disabled when the
  // configured binding has gone stale instead of surfacing a guaranteed NO_AI_KEY.
  bool get _configuredForLaunch =>
      isProjectAiProviderBindingAvailable(_resolvedKeyId, _availableKeys);

  bool get _hasStaleProviderBinding =>
      _availableKeysLoaded &&
      _resolvedKeyId != null &&
      !_configuredForLaunch;

  bool get _isError => _status == 'error';

  bool get _showReload {
    if (_busy) return false;
    if (!_hasPod) return false;
    if (_isError) return true;
    return projectShowsContainerError({
      'status': _status,
      'observed_state': _runtime?['observed_state'],
    });
  }

  bool get _launched =>
      _status == 'active' || _status == 'paused' || _status == 'error' || _status == 'completed';

  bool get _containerUnhealthy {
    final container = {
      'runtime': _runtime,
      'status': _status,
      'last_error': _runtime?['last_error'],
      'observed_state': _runtime?['observed_state'],
    };
    return _isError ||
        (_status == 'active' &&
            !containerIsPaused(container) &&
            !containerRuntimeHealthy(container));
  }

  bool get _showLaunch {
    if (_busy) return false;
    if (_launched) return false;
    if (_hasPod) return false;
    if (!_configuredForLaunch) return false;
    if (_showReload) return false;
    if (containerIsInFlight({
      'runtime': _runtime,
      'observed_state': _runtime?['observed_state'],
    })) {
      return false;
    }
    return true;
  }

  bool get _aiProviderNeedsSelection =>
      _hasStaleProviderBinding ||
      (_availableKeys.isNotEmpty && _selectedKeyName() == null);

  String? _selectedKeyName() {
    for (final k in _availableKeys) {
      if (k['id'] == _resolvedKeyId) {
        return k['name'] as String? ?? _resolvedKeyId;
      }
    }
    return null;
  }

  Future<void> _saveName(String v) async {
    final name = v.trim();
    if (name.isEmpty) return;
    await workContext.api.patchProject(projectId: widget.projectId, name: name);
    if (mounted) setState(() => _name = name);
  }

  Future<void> _saveKey(String keyId) async {
    await workContext.api.patchProject(
      projectId: widget.projectId,
      resolvedAiKeyId: keyId,
    );
    if (mounted) setState(() => _resolvedKeyId = keyId);
  }

  Future<void> _pickAiKey() async {
    if (_availableKeys.isEmpty) return;
    final picked = await ProjectAiKeySelectPage.push(
      context,
      keys: _availableKeys,
      selectedKeyId: _resolvedKeyId,
    );
    if (picked == null || !mounted) return;
    try {
      await _saveKey(picked);
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  void _openAbout() {
    Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => ProjectAboutPage(
          projectId: widget.projectId,
          projectName: _name,
        ),
      ),
    );
  }

  void _openDialogs() {
    Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => ProjectDialogsPage(
          cabinetId: widget.cabinetId,
          projectId: widget.projectId,
          projectName: _name.isNotEmpty ? _name : widget.projectId,
        ),
      ),
    ).then((_) => workContext.notifyProjectLifecycleChanged());
  }

  void _openModules() {
    Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => ProjectModulesListPage(
          cabinetId: widget.cabinetId,
          projectId: widget.projectId,
          projectName: _name,
        ),
      ),
    );
  }

  Future<void> _launch() async {
    await runProjectWakeFlow(
      context: context,
      action: ProjectWakeAction.launch,
      store: appJobStore,
      api: workContext.api,
      projectId: widget.projectId,
      successSnack: (l10n) => l10n.projectLaunchSuccess,
      onSettled: _load,
    );
  }

  Future<void> _joinInFlight() async {
    final l10n = AppLocalizations.of(context);
    try {
      await joinInFlightProjectStart(
        store: appJobStore,
        api: workContext.api,
        projectId: widget.projectId,
        l10n: l10n,
      );
    } catch (_) {
      // job status carries the error
    } finally {
      if (mounted) await _load();
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

  void _openContainer() {
    Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => ProjectContainerPage(
          projectId: widget.projectId,
          projectName: _name,
        ),
      ),
    ).then((_) => _load());
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

  void _openManagement() {
    Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => ProjectManagementPage(
          projectId: widget.projectId,
          projectName: _name,
        ),
      ),
    ).then((_) => _load());
  }

  Future<void> _resumeProject() async {
    await runProjectWakeFlow(
      context: context,
      action: ProjectWakeAction.resume,
      store: appJobStore,
      api: workContext.api,
      projectId: widget.projectId,
      onSettled: _load,
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    if (_loading && _name.isEmpty) {
      return AppScaffold(
        title: Text(_name.isNotEmpty ? _name : l10n.projectProjectSettings),
        body: const Center(child: CircularProgressIndicator()),
      );
    }

    final paused = containerIsPaused({
      'status': _status,
      'runtime': _runtime,
      'observed_state': _runtime?['observed_state'],
    });
    final selectedName = _selectedKeyName();
    final warning = context.appColors.warning;
    final error = context.appColors.danger;
    final success = context.appColors.success;
    final job = _job;
    final showProgress = _busy && job != null;

    return AppScaffold(
      title: Text(_name.isNotEmpty ? _name : l10n.projectProjectSettings),
      body: ListView(
        padding: EdgeInsets.all(AppSpacing.md),
        children: [
          Padding(
            padding: EdgeInsets.only(bottom: AppSpacing.md),
            child: ProjectMetricsWrap(metrics: _metrics, showLastActivity: false),
          ),
          AppValuePreference<String>(
            title: l10n.projectProjectName,
            icon: Icons.title_outlined,
            value: _name,
            onSave: _saveName,
          ),
          if (_launched &&
              projectChatSendable({
                'status': _status,
                'runtime': _runtime,
                'observed_state': _runtime?['observed_state'],
              }))
            AppNavPreference(
              title: l10n.projectDialogsLabel,
              icon: Icons.forum_outlined,
              onTap: _openDialogs,
            ),
          AppNavPreference(
            title: l10n.projectAboutLabel,
            icon: Icons.info_outline,
            onTap: _openAbout,
          ),
          if (_availableKeys.isNotEmpty || _resolvedKeyId != null)
            AppPreferenceTile(
              title: l10n.projectPreferredAgentProvider,
              icon: Icons.smart_toy_outlined,
              accentColor: _aiProviderNeedsSelection ? warning : null,
              subtitle: Text(
                _hasStaleProviderBinding
                    ? (_availableKeys.isEmpty
                          ? l10n.projectAiProviderUnavailableNoOptions
                          : l10n.projectAiProviderUnavailableReselect)
                    : selectedName ?? l10n.projectAiProviderNotSelected,
                style: _aiProviderNeedsSelection
                    ? TextStyle(color: warning)
                    : null,
              ),
              trailing: _availableKeys.isEmpty
                  ? null
                  : const AppTrailingChevron(),
              onTap: _availableKeys.isEmpty ? null : _pickAiKey,
            ),
          AppNavPreference(
            title: l10n.projectModulesLabel,
            icon: Icons.extension_outlined,
            enabled: !_busy,
            onTap: _openModules,
          ),
          AppNavPreference(
            title: _scheduleLabel(context),
            icon: Icons.schedule_outlined,
            enabled: !_busy,
            onTap: _openSchedules,
          ),
          // Single lifecycle progress control — replaces Launch / Reload / Resume.
          if (showProgress)
            AppNavPreference(
              title: job.title.isNotEmpty ? job.title : l10n.projectLaunchInProgress,
              icon: Icons.hourglass_top_outlined,
              accentColor: warning,
              loading: true,
              loadingLabel: job.subtitle.isNotEmpty
                  ? job.subtitle
                  : l10n.projectLaunchStartingSnack,
              onTap: () {},
            )
          else ...[
            if (_showLaunch)
              AppNavPreference(
                title: l10n.projectLaunchProject,
                icon: Icons.rocket_launch_outlined,
                accentColor: success,
                onTap: _launch,
              ),
            if (_showReload)
              AppNavPreference(
                title: l10n.projectReload,
                icon: Icons.refresh_outlined,
                accentColor: warning,
                onTap: _reload,
              ),
            if (_launched && paused && _hasPod)
              AppNavPreference(
                title: l10n.projectResumeProject,
                icon: Icons.play_arrow_outlined,
                accentColor: warning,
                onTap: _resumeProject,
              ),
          ],
          if (_launched || _hasPod)
            AppNavPreference(
              title: l10n.projectContainer,
              icon: Icons.dns_outlined,
              accentColor: _containerUnhealthy ? error : null,
              enabled: !_busy,
              onTap: _openContainer,
            ),
          if (_launched && !paused && _hasPod && !_showReload && !_containerUnhealthy)
            AppNavPreference(
              title: l10n.projectProjectManagement,
              icon: Icons.tune_outlined,
              enabled: !_busy,
              onTap: _openManagement,
            ),
        ],
      ),
    );
  }
}
