import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_form.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_text_field.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';
import 'package:prodavan/features/employee/project_workspace_page.dart';

/// Full-page project create (L05 ux — no modals).
class ProjectCreatePage extends StatefulWidget {
  const ProjectCreatePage({super.key, required this.cabinetId});

  final String cabinetId;

  @override
  State<ProjectCreatePage> createState() => _ProjectCreatePageState();
}

class _ProjectCreatePageState extends State<ProjectCreatePage> {
  final _formKey = GlobalKey<FormState>();
  final _nameCtrl = TextEditingController(text: 'New project');
  bool _saving = false;
  String? _error;

  @override
  void dispose() {
    _nameCtrl.dispose();
    super.dispose();
  }

  Future<void> _create() async {
    if (!_formKey.currentState!.validate()) return;
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      final project = await workContext.api.createProject(
        cabinetId: widget.cabinetId,
        name: _nameCtrl.text.trim(),
      );
      if (!mounted) return;
      final projectId = project['id'] as String;
      final projectName = project['name'] as String? ?? _nameCtrl.text.trim();
      workContext.enterProject(projectId);
      Navigator.of(context).pushReplacement(
        MaterialPageRoute<void>(
          builder: (_) => ProjectWorkspacePage(
            projectId: projectId,
            projectName: projectName,
            cabinetId: widget.cabinetId,
          ),
        ),
      );
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _saving = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return AppScaffold(
      title: const Text('Create project'),
      body: ListView(
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
              AppButton(
                label: _saving ? 'Creating…' : 'Create and open chat',
                onPressed: _saving ? null : _create,
              ),
            ],
          ),
        ],
      ),
    );
  }
}
