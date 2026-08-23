import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_form.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_text_field.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';

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
  String? _error;

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
                      onPressed: _saving ? null : _save,
                    ),
                  ],
                ),
              ],
            ),
    );
  }
}
