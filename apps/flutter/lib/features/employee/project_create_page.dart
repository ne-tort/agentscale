import 'package:flutter/material.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/core/widgets/app_text_field.dart';
import 'package:prodavan/features/employee/project_workspace_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Full-page project create (L05 ux — no modals).
class ProjectCreatePage extends StatefulWidget {
  const ProjectCreatePage({super.key, required this.cabinetId});

  final String cabinetId;

  @override
  State<ProjectCreatePage> createState() => _ProjectCreatePageState();
}

class _ProjectCreatePageState extends State<ProjectCreatePage> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      if (_nameCtrl.text.isEmpty) {
        _nameCtrl.text = AppLocalizations.of(context).projectNewProject;
      }
    });
  }

  static const _providers = <String?>[null, 'cursor', 'codex', 'claude_code'];

  final _formKey = GlobalKey<FormState>();
  final _nameCtrl = TextEditingController();
  String? _agentProvider;
  bool _saving = false;
  Object? _error;

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
        agentProvider: _agentProvider,
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
        _error = e;
        _saving = false;
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
      title: Text(l10n.projectCreateProject),
      body: ListView(
        padding: const EdgeInsets.all(AppSpacing.lg),
        children: [
          if (_error != null) AppStatusBanner(severity: AppStatusSeverity.error, message: AppErrors.localize(context, _error!)),
          Form(
            key: _formKey,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                AppTextField(
                  controller: _nameCtrl,
                  label: l10n.projectProjectName,
                  enabled: !_saving,
                  validator: (v) {
                    if (v == null || v.trim().isEmpty) return l10n.commonNameRequired;
                    return null;
                  },
                ),
                SizedBox(height: AppSpacing.md),
                AppChoicePreference<String?>(
                  title: l10n.projectPreferredAgentProvider,
                  icon: Icons.smart_toy_outlined,
                  value: _agentProvider,
                  choices: _providers,
                  keyFor: _keyFor,
                  labelFor: _labelFor,
                  enabled: !_saving,
                  onSave: (v) async => setState(() => _agentProvider = v),
                ),
                AppAsyncButton(
                  label: _saving ? l10n.commonCreating : l10n.projectCreateAndOpenChat,
                  busy: _saving,
                  onPressed: _saving ? null : _create,
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
