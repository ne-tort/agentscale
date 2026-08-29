import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_confirm_page.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_snack_bar.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Project settings — name, about, launch/pause/resume, provider, AI key, modules.
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
  static const _providers = <String?>[null, 'cursor', 'codex', 'claude_code'];

  String _name = '';
  String _about = '';
  String? _agentProvider;
  String? _resolvedKeyId;
  String? _creatorName;
  String? _status;
  bool _hasPod = false;
  List<Map<String, dynamic>> _availableKeys = const [];
  List<Map<String, dynamic>> _cabinetModules = const [];
  List<String> _enabledModuleIds = const [];
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
      final modules = await workContext.api.listCabinetModules(widget.cabinetId);
      List<String> enabled = const [];
      try {
        enabled = await workContext.api.listProjectModuleIds(widget.projectId);
      } catch (_) {
        enabled = modules
            .map((m) => m['module_id'] as String? ?? m['id'] as String? ?? '')
            .where((id) => id.isNotEmpty)
            .toList();
      }
      if (!mounted) return;
      setState(() {
        _name = project['name'] as String? ?? '';
        _about = project['about'] as String? ?? '';
        _agentProvider = project['agent_provider'] as String?;
        _resolvedKeyId = project['resolved_ai_key_id'] as String?;
        _creatorName = project['created_by_login'] as String? ??
            project['created_by_employee_id'] as String?;
        _status = project['status'] as String? ?? 'draft';
        _hasPod = project['runtime'] != null;
        _availableKeys = keys;
        _cabinetModules = modules;
        _enabledModuleIds = enabled;
        if (_resolvedKeyId == null && keys.isNotEmpty) {
          _resolvedKeyId = keys.first['id'] as String?;
        }
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      AppErrors.showSnack(context, e);
    }
  }

  bool get _configuredForLaunch =>
      (_agentProvider ?? '').isNotEmpty && (_resolvedKeyId ?? '').isNotEmpty;

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

  Future<void> _saveProvider(String? provider) async {
    await workContext.api.patchProject(
      projectId: widget.projectId,
      agentProvider: provider,
      clearAgentProvider: provider == null,
    );
    if (mounted) setState(() => _agentProvider = provider);
  }

  Future<void> _saveKey(String? keyId) async {
    await workContext.api.patchProject(
      projectId: widget.projectId,
      resolvedAiKeyId: keyId,
      clearResolvedAiKeyId: keyId == null,
    );
    if (mounted) setState(() => _resolvedKeyId = keyId);
  }

  Future<void> _launch() async {
    setState(() => _busy = true);
    try {
      await workContext.api.launchProject(widget.projectId);
      if (!mounted) return;
      AppSnackBar.success(context, AppLocalizations.of(context).projectLaunchSuccess);
      await _load();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
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

  Future<void> _saveModules(List<String> ids) async {
    await workContext.api.patchProjectModules(widget.projectId, moduleIds: ids);
    if (mounted) setState(() => _enabledModuleIds = ids);
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    if (_loading) {
      return AppScaffold(
        title: Text(l10n.projectProjectSettings),
        body: const Center(child: CircularProgressIndicator()),
      );
    }

    final paused = _status == 'paused';
    final keyOptions = _availableKeys
        .map((k) => k['id'] as String)
        .whereType<String>()
        .toList();
    final keyLabels = {
      for (final k in _availableKeys)
        k['id'] as String: k['name'] as String? ?? k['id'] as String,
    };

    return AppScaffold(
      title: Text(l10n.projectProjectSettings),
      body: ListView(
        padding: EdgeInsets.all(AppSpacing.md),
        children: [
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
          AppChoicePreference<String?>(
            title: l10n.projectPreferredAgentProvider,
            icon: Icons.smart_toy_outlined,
            value: _agentProvider,
            choices: _providers,
            keyFor: (v) => v ?? '__default__',
            labelFor: (v) => v ?? l10n.projectCompanyDefault,
            onSave: _saveProvider,
          ),
          if (keyOptions.isNotEmpty)
            AppChoicePreference<String?>(
              title: l10n.projectAiKeyLabel,
              icon: Icons.key_outlined,
              value: _resolvedKeyId,
              choices: [null, ...keyOptions],
              keyFor: (v) => v ?? '__auto__',
              labelFor: (v) => v == null ? l10n.commonAuto : (keyLabels[v] ?? v),
              onSave: _saveKey,
            ),
          if (_cabinetModules.isNotEmpty)
            AppMultiChoicePreference<String>(
              title: l10n.projectModulesLabel,
              icon: Icons.extension_outlined,
              values: _enabledModuleIds.toSet(),
              choices: _cabinetModules
                  .map((m) => m['module_id'] as String? ?? m['id'] as String? ?? '')
                  .where((id) => id.isNotEmpty)
                  .toList(),
              keyFor: (id) => id,
              labelFor: (id) {
                final mod = _cabinetModules.firstWhere(
                  (m) => (m['module_id'] ?? m['id']) == id,
                  orElse: () => {'name': id},
                );
                return mod['name'] as String? ?? id;
              },
              onSave: (ids) => _saveModules(ids.toList()),
            ),
          if (!_hasPod && !_configuredForLaunch)
            Padding(
              padding: EdgeInsets.only(bottom: AppSpacing.sm),
              child: AppStatusBanner(
                severity: AppStatusSeverity.info,
                message: l10n.projectConfigureBeforeLaunch,
              ),
            ),
          if (!_hasPod && _configuredForLaunch)
            AppNavPreference(
              title: l10n.projectLaunchProject,
              icon: Icons.rocket_launch_outlined,
              enabled: !_busy,
              onTap: _launch,
            ),
          if (_hasPod) ...[
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
