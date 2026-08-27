import 'package:flutter/material.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
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
  static const _providers = <String?>[null, 'cursor', 'codex', 'claude_code'];

  String _name = '';
  String? _agentProvider;
  bool _saving = false;
  bool _seeded = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (!_seeded) {
      _seeded = true;
      _name = AppLocalizations.of(context).projectNewProject;
    }
  }

  Future<void> _create() async {
    final l10n = AppLocalizations.of(context);
    final name = _name.trim();
    if (name.isEmpty) {
      AppErrors.showSnack(context, l10n.commonNameRequired);
      return;
    }
    setState(() => _saving = true);
    try {
      final project = await workContext.api.createProject(
        cabinetId: widget.cabinetId,
        name: name,
        agentProvider: _agentProvider,
      );
      if (!mounted) return;
      final projectId = project['id'] as String;
      final projectName = project['name'] as String? ?? name;
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
      setState(() => _saving = false);
      AppErrors.showSnack(context, e);
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
        padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
        children: [
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
            enabled: !_saving,
            onSave: (v) async => setState(() => _agentProvider = v),
          ),
          if (_saving)
            const Padding(
              padding: EdgeInsets.all(AppSpacing.lg),
              child: Center(child: CircularProgressIndicator(strokeWidth: 2)),
            )
          else
            AppNavPreference(
              title: l10n.projectCreateAndOpenChat,
              icon: Icons.chat_outlined,
              onTap: _create,
            ),
        ],
      ),
    );
  }
}
