import 'package:flutter/material.dart';

import 'package:prodavan/core/containers/container_runtime_presenter.dart';
import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_confirm_page.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_snack_bar.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/core/widgets/app_trailing_chevron.dart';
import 'package:prodavan/core/widgets/project_metrics_wrap.dart';
import 'package:prodavan/features/employee/project_ai_key_select_page.dart';
import 'package:prodavan/features/employee/project_container_page.dart';
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
  String? _resolvedKeyId;
  String? _creatorName;
  String? _status;
  Map<String, dynamic>? _runtime;
  bool _hasPod = false;
  List<Map<String, dynamic>> _availableKeys = const [];
  Map<String, dynamic>? _metrics;
  bool _loading = true;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
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

  bool get _launched =>
      _status == 'active' || _status == 'paused' || _status == 'error' || _status == 'completed';

  bool get _containerUnhealthy =>
      _isError ||
      (_status == 'active' &&
          !containerRuntimeHealthy({'runtime': _runtime, 'status': _status, 'last_error': _runtime?['last_error']}));

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
    setState(() => _busy = true);
    try {
      final result = await workContext.api.launchProject(widget.projectId);
      if (!mounted) return;
      if (result['status'] == 'error') {
        await _load();
        return;
      }
      AppSnackBar.success(context, AppLocalizations.of(context).projectLaunchSuccess);
      await _load();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _reload() async {
    setState(() => _busy = true);
    try {
      await workContext.api.reloadProject(widget.projectId);
      if (!mounted) return;
      await _load();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
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

  Future<void> _togglePause() async {
    setState(() => _busy = true);
    try {
      if (_status == 'paused') {
        await workContext.api.resumeProject(widget.projectId);
      } else {
        await workContext.api.pauseProject(widget.projectId);
      }
      await _load();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _syncProject() async {
    setState(() => _busy = true);
    try {
      await workContext.api.syncProject(widget.projectId);
      if (!mounted) return;
      AppSnackBar.success(context, AppLocalizations.of(context).projectUpdateSuccess);
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
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
          AppValuePreference<String>(
            title: l10n.projectAboutLabel,
            icon: Icons.notes_outlined,
            value: _about,
            onSave: _saveAbout,
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
          if (!_launched && _configuredForLaunch && !_isError)
            AppNavPreference(
              title: l10n.projectLaunchProject,
              icon: Icons.rocket_launch_outlined,
              enabled: !_busy,
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
          if (_isError)
            AppNavPreference(
              title: l10n.projectReload,
              icon: Icons.refresh_outlined,
              enabled: !_busy,
              onTap: _reload,
            ),
          if (_hasPod && !_isError) ...[
            AppNavPreference(
              title: paused ? l10n.projectResumeProject : l10n.projectPauseProject,
              icon: paused ? Icons.play_arrow_outlined : Icons.pause_outlined,
              enabled: !_busy,
              onTap: _togglePause,
            ),
            AppNavPreference(
              title: l10n.projectUpdateProject,
              icon: Icons.sync_outlined,
              enabled: !_busy,
              onTap: _syncProject,
            ),
            AppNavPreference(
              title: l10n.projectResetAgent,
              icon: Icons.restart_alt_outlined,
              enabled: !_busy,
              onTap: _resetAgent,
            ),
          ],
        ],
      ),
    );
  }
}
