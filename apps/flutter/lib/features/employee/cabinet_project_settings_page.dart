import 'dart:async';

import 'package:flutter/material.dart';

import 'package:prodavan/core/containers/container_runtime_presenter.dart';
import 'package:prodavan/core/containers/project_container_poll.dart';
import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_snack_bar.dart';
import 'package:prodavan/core/widgets/app_trailing_chevron.dart';
import 'package:prodavan/core/widgets/project_metrics_wrap.dart';
import 'package:prodavan/features/employee/project_ai_key_select_page.dart';
import 'package:prodavan/features/employee/project_container_page.dart';
import 'package:prodavan/features/employee/project_management_page.dart';
import 'package:prodavan/features/employee/project_modules_list_page.dart';
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
  String _about = '';
  String _budget = '';
  String? _resolvedKeyId;
  String? _creatorName;
  String? _status;
  Map<String, dynamic>? _runtime;
  bool _hasPod = false;
  List<Map<String, dynamic>> _availableKeys = const [];
  Map<String, dynamic>? _metrics;
  bool _loading = true;
  bool _launching = false;
  bool _reloading = false;
  bool _resuming = false;

  bool get _busy => _launching || _reloading || _resuming;

  @override
  void initState() {
    super.initState();
    unawaited(
      workContext.selectProject(
        cabinetId: widget.cabinetId,
        projectId: widget.projectId,
      ),
    );
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final project = await workContext.api.getProject(widget.projectId);
      List<Map<String, dynamic>> keys = const [];
      try {
        keys = await workContext.api.listProjectAiKeys(widget.projectId);
      } catch (_) {}
      Map<String, dynamic>? metrics;
      try {
        metrics = await workContext.api.getProjectMetrics(widget.projectId);
      } catch (_) {}
      if (!mounted) return;

      var resolvedKeyId = project['resolved_ai_key_id'] as String?;
      if (resolvedKeyId == null && keys.length == 1) {
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
        _about = project['about'] as String? ?? '';
        final budgetTokens = project['budget_tokens'];
        _budget = budgetTokens == null ? '' : '$budgetTokens';
        _resolvedKeyId = resolvedKeyId;
        _creatorName = project['created_by_login'] as String? ??
            project['created_by_employee_id'] as String?;
        _status = project['status'] as String? ?? 'draft';
        _runtime = project['runtime'] is Map
            ? Map<String, dynamic>.from(project['runtime'] as Map)
            : null;
        _hasPod = _runtime != null;
        _availableKeys = keys;
        _metrics = metrics;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      AppErrors.showSnack(context, e);
    }
  }

  bool get _configuredForLaunch => (_resolvedKeyId ?? '').isNotEmpty;

  bool get _isError => _status == 'error';

  bool get _showReload {
    if (!_hasPod) return false;
    if (_isError) return true;
    return projectShowsContainerError({
      'status': _status,
      'observed_state': _runtime?['observed_state'],
    });
  }

  bool get _launched =>
      _status == 'active' || _status == 'paused' || _status == 'error' || _status == 'completed';

  bool get _containerUnhealthy =>
      _isError ||
      (_status == 'active' &&
          !containerRuntimeHealthy({
            'runtime': _runtime,
            'status': _status,
            'last_error': _runtime?['last_error'],
            'observed_state': _runtime?['observed_state'],
          }));

  bool get _aiProviderNeedsSelection =>
      _availableKeys.length >= 2 && (_resolvedKeyId ?? '').isEmpty;

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

  Future<void> _saveAbout(String v) async {
    await workContext.api.patchProject(projectId: widget.projectId, about: v.trim());
    if (mounted) setState(() => _about = v.trim());
  }

  Future<void> _saveBudget(String v) async {
    final trimmed = v.trim();
    final tokens = trimmed.isEmpty ? null : int.parse(trimmed);
    await workContext.api.patchProject(
      projectId: widget.projectId,
      budgetTokens: tokens,
      updateBudgetTokens: true,
    );
    if (mounted) setState(() => _budget = trimmed);
  }

  bool _validBudgetInput(String raw) {
    final trimmed = raw.trim();
    if (trimmed.isEmpty) return true;
    final n = int.tryParse(trimmed);
    return n != null && n >= 0;
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
    final l10n = AppLocalizations.of(context);
    AppSnackBar.info(context, l10n.projectLaunchStartingSnack);
    setState(() => _launching = true);
    try {
      final result = await workContext.api.launchProject(widget.projectId);
      if (!mounted) return;
      if (result['status'] == 'error') {
        await _load();
        return;
      }
      AppSnackBar.success(context, l10n.projectLaunchSuccess);
      await _load();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    } finally {
      if (mounted) setState(() => _launching = false);
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
    final l10n = AppLocalizations.of(context);
    AppSnackBar.info(context, l10n.projectResumeStartingSnack);
    setState(() => _resuming = true);
    try {
      await workContext.api.resumeProject(widget.projectId);
      final container = await pollProjectContainerUntilSettled(
        api: workContext.api,
        projectId: widget.projectId,
      );
      if (!mounted) return;
      final failure = containerObservedFailureMessage(container);
      if (failure != null) {
        AppErrors.showSnack(context, failure);
      }
      await _load();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    } finally {
      if (mounted) setState(() => _resuming = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    if (_loading) {
      return AppScaffold(
        title: Text(_name.isNotEmpty ? _name : l10n.projectProjectSettings),
        body: const Center(child: CircularProgressIndicator()),
      );
    }

    final paused = _status == 'paused';
    final selectedName = _selectedKeyName();
    final warning = context.appColors.warning;
    final error = context.appColors.danger;
    final success = context.appColors.success;

    return AppScaffold(
      title: Text(_name),
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
              projectChatReadable({
                'status': _status,
                'runtime': _runtime,
                'observed_state': _runtime?['observed_state'],
              }))
            AppPreferenceTile(
              title: l10n.projectOpenChat,
              icon: Icons.chat_bubble_outline,
              subtitle: Text(l10n.chatOpenFromSidebarHint),
              trailing: const AppTrailingChevron(),
              onTap: () async {
                // Multi-chat: select this project for the rail, then return to shell.
                try {
                  await workContext.selectProject(
                    cabinetId: widget.cabinetId,
                    projectId: widget.projectId,
                  );
                } catch (_) {
                  /* selection is best-effort */
                }
                if (!mounted) return;
                Navigator.of(context).pop();
              },
            ),
          AppValuePreference<String>(
            title: l10n.projectAboutLabel,
            icon: Icons.notes_outlined,
            value: _about,
            onSave: _saveAbout,
          ),
          AppValuePreference<String>(
            title: l10n.projectBudgetLabel,
            icon: Icons.account_balance_wallet_outlined,
            value: _budget,
            digitsOnly: true,
            validateInput: _validBudgetInput,
            invalidMessage: l10n.errorValidation,
            onSave: _saveBudget,
          ),
          AppValuePreference<String>(
            title: l10n.projectCreatorLabel,
            icon: Icons.person_outline,
            value: _creatorName ?? '—',
            enabled: false,
            onSave: (_) async {},
          ),
          if (_availableKeys.isNotEmpty)
            AppPreferenceTile(
              title: l10n.projectPreferredAgentProvider,
              icon: Icons.smart_toy_outlined,
              accentColor: _aiProviderNeedsSelection ? warning : null,
              subtitle: Text(
                selectedName ?? l10n.projectAiProviderNotSelected,
                style: _aiProviderNeedsSelection
                    ? TextStyle(color: warning)
                    : null,
              ),
              trailing: const AppTrailingChevron(),
              onTap: _pickAiKey,
            ),
          AppNavPreference(
            title: l10n.projectModulesLabel,
            icon: Icons.extension_outlined,
            enabled: !_busy,
            onTap: _openModules,
          ),
          if (!_launched && _configuredForLaunch && !_showReload)
            AppNavPreference(
              title: l10n.projectLaunchProject,
              icon: Icons.rocket_launch_outlined,
              enabled: !_busy,
              loading: _launching,
              loadingLabel: l10n.projectLaunchInProgress,
              accentColor: success,
              onTap: _launch,
            ),
          if (_launched)
            AppNavPreference(
              title: l10n.projectContainer,
              icon: Icons.dns_outlined,
              accentColor: _containerUnhealthy ? error : null,
              enabled: !_busy,
              onTap: _openContainer,
            ),
          if (_showReload)
            AppNavPreference(
              title: l10n.projectReload,
              icon: Icons.refresh_outlined,
              accentColor: warning,
              enabled: !_busy,
              loading: _reloading,
              loadingLabel: l10n.projectReload,
              onTap: _reload,
            ),
          if (_launched && paused && _hasPod)
            AppNavPreference(
              title: l10n.projectResumeProject,
              icon: Icons.play_arrow_outlined,
              accentColor: warning,
              enabled: !_busy,
              loading: _resuming,
              loadingLabel: l10n.projectResumeInProgress,
              onTap: _resumeProject,
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
