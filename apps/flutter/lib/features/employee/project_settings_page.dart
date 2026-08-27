import 'package:flutter/material.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_snack_bar.dart';
import 'package:prodavan/features/employee/widgets/project_status_chip.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Project settings — name + preferred agent provider override (L07/L03).
class ProjectSettingsPage extends StatefulWidget {
  const ProjectSettingsPage({
    super.key,
    required this.projectId,
    required this.projectName,
  });

  final String projectId;
  final String projectName;

  @override
  State<ProjectSettingsPage> createState() => _ProjectSettingsPageState();
}

class _ProjectSettingsPageState extends State<ProjectSettingsPage> {
  static const _providers = <String?>[null, 'cursor', 'codex', 'claude_code'];

  String _name = '';
  String? _agentProvider;
  bool _loading = true;
  bool _saving = false;
  bool _rematerializing = false;
  bool _pausing = false;
  String? _projectStatus;
  String? _rematerializeInfo;

  @override
  void initState() {
    super.initState();
    _name = widget.projectName;
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final project = await workContext.api.getProject(widget.projectId);
      if (!mounted) return;
      setState(() {
        _name = project['name'] as String? ?? widget.projectName;
        _agentProvider = project['agent_provider'] as String?;
        _projectStatus = project['status'] as String? ?? 'active';
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      AppErrors.showSnack(context, e);
    }
  }

  Future<void> _save() async {
    final l10n = AppLocalizations.of(context);
    final name = _name.trim();
    if (name.isEmpty) {
      AppErrors.showSnack(context, l10n.commonNameRequired);
      return;
    }
    setState(() => _saving = true);
    try {
      await workContext.api.patchProject(
        projectId: widget.projectId,
        name: name,
        agentProvider: _agentProvider,
        clearAgentProvider: _agentProvider == null,
      );
      if (!mounted) return;
      Navigator.of(context).pop(name);
    } catch (e) {
      if (!mounted) return;
      setState(() => _saving = false);
      AppErrors.showSnack(context, e);
    }
  }

  Future<void> _rematerialize() async {
    setState(() {
      _rematerializing = true;
      _rematerializeInfo = null;
    });
    try {
      final l10n = AppLocalizations.of(context);
      final result = await workContext.api.rematerializeProject(widget.projectId);
      if (!mounted) return;
      final packages = (result['package_names'] as List?)?.join(', ') ?? '';
      setState(() {
        _rematerializing = false;
        _rematerializeInfo = packages.isEmpty
            ? l10n.projectWorkspaceRematerializedNoPackages
            : l10n.projectRematerializedPackages(packages);
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _rematerializing = false);
      AppErrors.showSnack(context, e);
    }
  }

  Future<void> _pause() async {
    setState(() => _pausing = true);
    try {
      final result = await workContext.api.pauseProject(widget.projectId);
      if (!mounted) return;
      final l10n = AppLocalizations.of(context);
      setState(() {
        _projectStatus = result['status'] as String? ?? 'paused';
        _pausing = false;
      });
      AppSnackBar.success(context, l10n.projectProjectPaused);
    } catch (e) {
      if (!mounted) return;
      setState(() => _pausing = false);
      AppErrors.showSnack(context, e);
    }
  }

  Future<void> _resume() async {
    setState(() => _pausing = true);
    try {
      final result = await workContext.api.resumeProject(widget.projectId);
      if (!mounted) return;
      final l10n = AppLocalizations.of(context);
      setState(() {
        _projectStatus = result['status'] as String? ?? 'active';
        _pausing = false;
      });
      AppSnackBar.success(context, l10n.projectProjectResumed);
    } catch (e) {
      if (!mounted) return;
      setState(() => _pausing = false);
      AppErrors.showSnack(context, e);
    }
  }

  String _labelFor(String? value) {
    final l10n = AppLocalizations.of(context);
    if (value == null) return l10n.projectCompanyDefault;
    return value;
  }

  String _keyFor(String? value) => value ?? '__default__';

  bool get _busy => _saving || _rematerializing || _pausing;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return AppScaffold(
      title: Text(l10n.projectProjectSettings),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
              children: [
                if (_projectStatus != null)
                  ListTile(
                    leading: ProjectStatusChip(status: _projectStatus!),
                    title: Text(l10n.projectProjectStatus),
                    subtitle: ProjectStatusChip.isPaused(_projectStatus)
                        ? Text(l10n.projectPausedDisabledHint)
                        : Text(_projectStatus!),
                  ),
                AppValuePreference<String>(
                  title: l10n.projectProjectName,
                  icon: Icons.folder_outlined,
                  value: _name,
                  enabled: !_busy,
                  presentValue: (v) => v.isEmpty ? l10n.commonNotSet : v,
                  onSave: (v) async => setState(() => _name = v.trim()),
                ),
                AppChoicePreference<String?>(
                  title: l10n.projectPreferredAgentProvider,
                  icon: Icons.smart_toy_outlined,
                  value: _agentProvider,
                  choices: _providers,
                  keyFor: _keyFor,
                  labelFor: _labelFor,
                  enabled: !_busy,
                  onSave: (v) async => setState(() => _agentProvider = v),
                ),
                if (_busy)
                  const Padding(
                    padding: EdgeInsets.all(AppSpacing.lg),
                    child: Center(
                      child: CircularProgressIndicator(strokeWidth: 2),
                    ),
                  )
                else ...[
                  AppNavPreference(
                    title: l10n.commonSave,
                    icon: Icons.save_outlined,
                    onTap: _save,
                  ),
                  if (_projectStatus == 'paused')
                    AppNavPreference(
                      title: l10n.projectResumeProject,
                      icon: Icons.play_arrow_rounded,
                      onTap: _resume,
                    )
                  else
                    AppNavPreference(
                      title: l10n.projectPauseProject,
                      icon: Icons.pause_rounded,
                      onTap: _pause,
                    ),
                  AppNavPreference(
                    title: l10n.projectRematerializeWorkspace,
                    icon: Icons.refresh_rounded,
                    onTap: _rematerialize,
                  ),
                ],
                if (_rematerializeInfo != null)
                  Padding(
                    padding: const EdgeInsets.all(AppSpacing.md),
                    child: Text(
                      _rematerializeInfo!,
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                  ),
              ],
            ),
    );
  }
}
