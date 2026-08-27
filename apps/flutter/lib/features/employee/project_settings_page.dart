import 'package:flutter/material.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
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
  Object? _error;
  String? _rematerializeInfo;

  @override
  void initState() {
    super.initState();
    _name = widget.projectName;
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
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
      setState(() {
        _error = e;
        _loading = false;
      });
    }
  }

  Future<void> _save() async {
    final l10n = AppLocalizations.of(context);
    final name = _name.trim();
    if (name.isEmpty) {
      setState(() => _error = l10n.commonNameRequired);
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
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
      setState(() {
        _error = e;
        _saving = false;
      });
    }
  }

  Future<void> _rematerialize() async {
    setState(() {
      _rematerializing = true;
      _error = null;
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
      setState(() {
        _error = e;
        _rematerializing = false;
      });
    }
  }

  Future<void> _pause() async {
    setState(() {
      _pausing = true;
      _error = null;
    });
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
      setState(() {
        _error = e;
        _pausing = false;
      });
    }
  }

  Future<void> _resume() async {
    setState(() {
      _pausing = true;
      _error = null;
    });
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
      setState(() {
        _error = e;
        _pausing = false;
      });
    }
  }

  String _labelFor(String? value) {
    final l10n = AppLocalizations.of(context);
    if (value == null) return l10n.projectCompanyDefault;
    return value;
  }

  String _keyFor(String? value) => value ?? '__default__';

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return AppScaffold(
      title: Text(l10n.projectProjectSettings),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.all(AppSpacing.lg),
              children: [
                if (_error != null) AppStatusBanner(severity: AppStatusSeverity.error, message: AppErrors.localize(context, _error!)),
                if (_projectStatus != null)
                  ListTile(
                    contentPadding: EdgeInsets.zero,
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
                  enabled: !_saving,
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
                  enabled: !_saving && !_rematerializing && !_pausing,
                  onSave: (v) async => setState(() => _agentProvider = v),
                ),
                Padding(
                  padding: const EdgeInsets.all(AppSpacing.md),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      AppButton(
                        label: _saving ? l10n.commonSaving : l10n.commonSave,
                        onPressed:
                            _saving || _rematerializing || _pausing ? null : _save,
                      ),
                      if (_projectStatus == 'paused')
                        AppButton(
                          label: _pausing
                              ? l10n.projectResuming
                              : l10n.projectResumeProject,
                          onPressed: _saving || _rematerializing || _pausing
                              ? null
                              : _resume,
                        )
                      else
                        AppButton(
                          label: _pausing
                              ? l10n.projectPausing
                              : l10n.projectPauseProject,
                          onPressed: _saving || _rematerializing || _pausing
                              ? null
                              : _pause,
                        ),
                      AppButton(
                        label: _rematerializing
                            ? l10n.projectRematerializing
                            : l10n.projectRematerializeWorkspace,
                        onPressed: _saving || _rematerializing || _pausing
                            ? null
                            : _rematerialize,
                      ),
                      if (_rematerializeInfo != null)
                        Padding(
                          padding: const EdgeInsets.only(top: AppSpacing.sm),
                          child: Text(
                            _rematerializeInfo!,
                            style: Theme.of(context).textTheme.bodySmall,
                          ),
                        ),
                    ],
                  ),
                ),
              ],
            ),
    );
  }
}
