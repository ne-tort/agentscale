import 'package:flutter/material.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Edit cabinet AGENTS.md source (materialize writes AGENTS.md + CLAUDE.md).
class CabinetAgentsEditPage extends StatefulWidget {
  const CabinetAgentsEditPage({
    super.key,
    required this.cabinetId,
  });

  final String cabinetId;

  @override
  State<CabinetAgentsEditPage> createState() => _CabinetAgentsEditPageState();
}

class _CabinetAgentsEditPageState extends State<CabinetAgentsEditPage> {
  final _ctrl = TextEditingController();
  bool _loading = true;
  bool _saving = false;
  Object? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final doc = await workContext.api.getWorkspaceDoc(
        cabinetId: widget.cabinetId,
        slug: 'agents',
      );
      if (!mounted) return;
      _ctrl.text = doc['body'] as String? ?? '';
      setState(() => _loading = false);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e;
        _loading = false;
      });
    }
  }

  Future<void> _save() async {
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await workContext.api.putWorkspaceDoc(
        cabinetId: widget.cabinetId,
        slug: 'agents',
        body: _ctrl.text,
      );
      if (!mounted) return;
      Navigator.of(context).pop(true);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e;
        _saving = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return AppScaffold(
      title: Text(l10n.cabinetAgentsMd),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.all(AppSpacing.lg),
              children: [
                Text(
                  l10n.cabinetAgentsMdHint,
                  style: Theme.of(context).textTheme.bodySmall?.copyWith(
                        color: context.appColors.muted,
                      ),
                ),
                const SizedBox(height: AppSpacing.md),
                if (_error != null) AppStatusBanner(severity: AppStatusSeverity.error, message: AppErrors.localize(context, _error!)),
                TextField(
                  controller: _ctrl,
                  maxLines: 18,
                  enabled: !_saving,
                  decoration: InputDecoration(
                    border: OutlineInputBorder(),
                    alignLabelWithHint: true,
                    labelText: l10n.cabinetAgentsInstructions,
                  ),
                ),
                const SizedBox(height: AppSpacing.md),
                AppButton(
                  label: _saving ? l10n.commonSaving : l10n.commonSave,
                  onPressed: _saving ? null : _save,
                ),
              ],
            ),
    );
  }
}
