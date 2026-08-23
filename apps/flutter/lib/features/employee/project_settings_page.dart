import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_form.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_text_field.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';
import 'package:prodavan/features/employee/widgets/project_status_chip.dart';

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

  final _formKey = GlobalKey<FormState>();
  late final TextEditingController _nameCtrl;
  String? _agentProvider;
  bool _loading = true;
  bool _saving = false;
  bool _rematerializing = false;
  bool _pausing = false;
  String? _projectStatus;
  String? _error;
  String? _rematerializeInfo;

  @override
  void initState() {
    super.initState();
    _nameCtrl = TextEditingController(text: widget.projectName);
    _load();
  }

  @override
  void dispose() {
    _nameCtrl.dispose();
    super.dispose();
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
        _nameCtrl.text = project['name'] as String? ?? widget.projectName;
        _agentProvider = project['agent_provider'] as String?;
        _projectStatus = project['status'] as String? ?? 'active';
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  Future<void> _save() async {
    if (!_formKey.currentState!.validate()) return;
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await workContext.api.patchProject(
        projectId: widget.projectId,
        name: _nameCtrl.text.trim(),
        agentProvider: _agentProvider,
        clearAgentProvider: _agentProvider == null,
      );
      if (!mounted) return;
      Navigator.of(context).pop(_nameCtrl.text.trim());
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
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
      final result = await workContext.api.rematerializeProject(widget.projectId);
      if (!mounted) return;
      final packages = (result['package_names'] as List?)?.join(', ') ?? '';
      setState(() {
        _rematerializing = false;
        _rematerializeInfo = packages.isEmpty
            ? 'Workspace rematerialized (no MCP packages)'
            : 'Rematerialized packages: $packages';
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
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
      setState(() {
        _projectStatus = result['status'] as String? ?? 'paused';
        _pausing = false;
      });
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Project paused')),
      );
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
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
      setState(() {
        _projectStatus = result['status'] as String? ?? 'active';
        _pausing = false;
      });
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Project resumed')),
      );
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _pausing = false;
      });
    }
  }

  String _labelFor(String? value) {
    if (value == null) return 'Company default';
    return value;
  }

  @override
  Widget build(BuildContext context) {
    return AppScaffold(
      title: const Text('Project settings'),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.all(AppSpacing.lg),
              children: [
                if (_error != null) InlineErrorBanner(message: _error!),
                if (_projectStatus != null)
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: ProjectStatusChip(status: _projectStatus!),
                    title: const Text('Project status'),
                    subtitle: ProjectStatusChip.isPaused(_projectStatus)
                        ? const Text('Chat and uploads are disabled while paused')
                        : Text(_projectStatus!),
                  ),
                AppForm(
                  formKey: _formKey,
                  children: [
                    AppTextField(
                      controller: _nameCtrl,
                      label: 'Project name',
                      enabled: !_saving,
                      validator: (v) {
                        if (v == null || v.trim().isEmpty) return 'Name required';
                        return null;
                      },
                    ),
                    DropdownButtonFormField<String?>(
                      value: _agentProvider,
                      decoration: const InputDecoration(
                        labelText: 'Preferred agent provider',
                        border: OutlineInputBorder(),
                      ),
                      items: [
                        for (final p in _providers)
                          DropdownMenuItem<String?>(
                            value: p,
                            child: Text(_labelFor(p)),
                          ),
                      ],
                      onChanged: _saving
                          ? null
                          : (v) => setState(() => _agentProvider = v),
                    ),
                    AppButton(
                      label: _saving ? 'Saving…' : 'Save',
                      onPressed: _saving || _rematerializing || _pausing ? null : _save,
                    ),
                    if (_projectStatus == 'paused')
                      AppButton(
                        label: _pausing ? 'Resuming…' : 'Resume project',
                        onPressed: _saving || _rematerializing || _pausing ? null : _resume,
                      )
                    else
                      AppButton(
                        label: _pausing ? 'Pausing…' : 'Pause project',
                        onPressed: _saving || _rematerializing || _pausing ? null : _pause,
                      ),
                    AppButton(
                      label: _rematerializing ? 'Rematerializing…' : 'Rematerialize workspace',
                      onPressed: _saving || _rematerializing || _pausing ? null : _rematerialize,
                    ),
                    if (_rematerializeInfo != null)
                      Text(
                        _rematerializeInfo!,
                        style: Theme.of(context).textTheme.bodySmall,
                      ),
                  ],
                ),
              ],
            ),
    );
  }
}
