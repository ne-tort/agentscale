import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_form.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_text_field.dart';
import 'package:prodavan/core/widgets/danger_confirm_page.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';

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
    final ok = await DangerConfirmPage.push(
      context,
      title: 'Archive table?',
      message:
          'Archive "${widget.tableSlug}". Rows stay in DB but table hides from lists. Remove views referencing this table first.',
      confirmLabel: 'Archive',
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
    final ok = await DangerConfirmPage.push(
      context,
      title: 'Delete table permanently?',
      message: 'Drop all data for "${widget.tableSlug}". This cannot be undone.',
      confirmLabel: 'Delete',
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
    final busy = _saving || _archiving || _deleting;
    return AppScaffold(
      title: const Text('Table settings'),
      body: ListView(
        padding: const EdgeInsets.all(AppSpacing.lg),
        children: [
          Text(widget.tableSlug, style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: AppSpacing.sm),
          if (_archived)
            Text(
              'Archived — you can delete permanently or go back.',
              style: Theme.of(context).textTheme.bodySmall?.copyWith(
                    color: Theme.of(context).colorScheme.onSurfaceVariant,
                  ),
            ),
          if (_archived) const SizedBox(height: AppSpacing.sm),
          if (_error != null) InlineErrorBanner(message: _error!),
          if (!_archived)
            AppForm(
              formKey: _formKey,
              children: [
                AppTextField(
                  controller: _label,
                  label: 'Label',
                  enabled: !busy,
                  validator: (v) => (v ?? '').trim().isEmpty ? 'Required' : null,
                ),
                AppButton(
                  label: _saving ? 'Saving…' : 'Save label',
                  onPressed: busy ? null : _saveLabel,
                ),
              ],
            ),
          if (!_archived) const Divider(height: 32),
          if (!_archived)
            AppButton(
              label: _archiving ? 'Archiving…' : 'Archive table',
              onPressed: busy ? null : _archive,
            ),
          if (_archived)
            AppButton(
              label: _deleting ? 'Deleting…' : 'Delete permanently',
              onPressed: busy ? null : _deletePermanently,
            ),
        ],
      ),
    );
  }
}
