import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Subpage — project description, budget, and creator.
class ProjectAboutPage extends StatefulWidget {
  const ProjectAboutPage({
    super.key,
    required this.projectId,
    required this.projectName,
  });

  final String projectId;
  final String projectName;

  @override
  State<ProjectAboutPage> createState() => _ProjectAboutPageState();
}

class _ProjectAboutPageState extends State<ProjectAboutPage> {
  String _about = '';
  String _budget = '';
  String? _creatorName;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final project = await workContext.api.getProject(widget.projectId);
      if (!mounted) return;
      setState(() {
        _about = project['about'] as String? ?? '';
        final budgetTokens = project['budget_tokens'];
        _budget = budgetTokens == null ? '' : '$budgetTokens';
        _creatorName = project['created_by_login'] as String? ??
            project['created_by_employee_id'] as String?;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      AppErrors.showSnack(context, e);
    }
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

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return AppScaffold(
      title: Text(l10n.projectAboutLabel),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: EdgeInsets.all(AppSpacing.md),
              children: [
                AppValuePreference<String>(
                  title: l10n.projectDescriptionLabel,
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
                  value: _creatorName ?? l10n.commonEmDash,
                  enabled: false,
                  onSave: (_) async {},
                ),
              ],
            ),
    );
  }
}
