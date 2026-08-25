import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/danger_confirm_page.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Rename or archive a meta table (L06).
class CabinetTableSettingsPage extends StatefulWidget {
  const CabinetTableSettingsPage({
    super.key,
    required this.cabinetId,
    required this.tableSlug,
    required this.tableLabel,
  });

  final String cabinetId;
  final String tableSlug;
  final String tableLabel;

  @override
  State<CabinetTableSettingsPage> createState() => _CabinetTableSettingsPageState();
}

class _CabinetTableSettingsPageState extends State<CabinetTableSettingsPage> {
  final _formKey = GlobalKey<FormState>();
  late final TextEditingController _label;
  bool _saving = false;
  bool _archiving = false;
  bool _deleting = false;
  bool _archived = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _label = TextEditingController(text: widget.tableLabel);
  }

  @override
  void dispose() {
    _label.dispose();
    super.dispose();
  }

  Future<void> _saveLabel() async {
    if (!_formKey.currentState!.validate()) return;
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await workContext.api.updateMetaTable(
        cabinetId: widget.cabinetId,
        tableSlug: widget.tableSlug,
        label: _label.text.trim(),
      );
      if (!mounted) return;
      Navigator.of(context).pop(true);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _saving = false;
      });
    }
  }

  Future<void> _archive() async {
    final l10n = AppLocalizations.of(context);
    final ok = await DangerConfirmPage.push(
      context,
      title: l10n.cabinetArchiveTableConfirm,
      message: l10n.cabinetArchiveTableMessage(widget.tableSlug),
      confirmLabel: l10n.commonArchive,
    );
    if (ok != true) return;

    setState(() {
      _archiving = true;
      _error = null;
    });
    try {
      await workContext.api.archiveMetaTable(
        cabinetId: widget.cabinetId,
        tableSlug: widget.tableSlug,
      );
      if (!mounted) return;
      setState(() {
        _archived = true;
        _archiving = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _archiving = false;
      });
    }
  }

  Future<void> _deletePermanently() async {
    final l10n = AppLocalizations.of(context);
    final ok = await DangerConfirmPage.push(
      context,
      title: l10n.cabinetDeleteTablePermanently,
      message: l10n.cabinetDropAllDataConfirm(widget.tableSlug),
      confirmLabel: l10n.commonDelete,
    );
    if (ok != true) return;

    setState(() {
      _deleting = true;
      _error = null;
    });
    try {
      await workContext.api.deleteMetaTable(
        cabinetId: widget.cabinetId,
        tableSlug: widget.tableSlug,
      );
      if (!mounted) return;
      Navigator.of(context).pop(true);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _deleting = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final busy = _saving || _archiving || _deleting;
    return AppScaffold(
      title: Text(l10n.cabinetTableSettings),
      body: ListView(
        padding: const EdgeInsets.all(AppSpacing.lg),
        children: [
          Text(widget.tableSlug, style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: AppSpacing.sm),
          if (_archived)
            Text(
              l10n.cabinetArchivedBanner,
              style: Theme.of(context).textTheme.bodySmall?.copyWith(
                    color: context.appColors.muted,
                  ),
            ),
          if (_archived) const SizedBox(height: AppSpacing.sm),
          if (_error != null) InlineErrorBanner(message: _error!),
          if (!_archived)
            Form(
              key: _formKey,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  TextFormField(
                    controller: _label,
                    decoration: InputDecoration(labelText: l10n.commonLabel),
                    enabled: !busy,
                    validator: (v) => (v ?? '').trim().isEmpty ? l10n.commonRequired : null,
                  ),
                  const SizedBox(height: AppSpacing.md),
                  AppButton(
                    label: _saving ? l10n.commonSaving : l10n.cabinetSaveLabel,
                    onPressed: busy ? null : _saveLabel,
                  ),
                ],
              ),
            ),
          if (!_archived) Divider(height: 32),
          if (!_archived)
            AppButton(
              label: _archiving ? l10n.cabinetArchiving : l10n.cabinetArchiveTable,
              onPressed: busy ? null : _archive,
            ),
          if (_archived)
            AppButton(
              label: _deleting ? l10n.commonDeleting : l10n.cabinetDeletePermanently,
              onPressed: busy ? null : _deletePermanently,
            ),
        ],
      ),
    );
  }
}
