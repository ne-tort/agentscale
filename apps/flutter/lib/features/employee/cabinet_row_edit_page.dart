import 'package:flutter/material.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Full-page meta row create/edit (L05 — no modals).
class CabinetRowEditPage extends StatefulWidget {
  const CabinetRowEditPage({
    super.key,
    required this.cabinetId,
    required this.tableSlug,
    required this.tableLabel,
    required this.columns,
    this.existing,
  });

  final String cabinetId;
  final String tableSlug;
  final String tableLabel;
  final List<Map<String, dynamic>> columns;
  final Map<String, dynamic>? existing;

  @override
  State<CabinetRowEditPage> createState() => _CabinetRowEditPageState();
}

class _CabinetRowEditPageState extends State<CabinetRowEditPage> {
  final _formKey = GlobalKey<FormState>();
  late final Map<String, TextEditingController> _controllers;
  bool _saving = false;
  Object? _error;

  @override
  void initState() {
    super.initState();
    final fields = _editableFields();
    if (fields.isEmpty && widget.existing == null) {
      fields.add('title');
    }
    _controllers = {
      for (final name in fields)
        name: TextEditingController(text: '${widget.existing?[name] ?? ''}'),
    };
  }

  List<String> _editableFields() {
    if (widget.columns.isNotEmpty) {
      return widget.columns.map((c) => c['name'] as String).where((n) => n.isNotEmpty).toList();
    }
    const skip = {'id', 'created_at'};
    if (widget.existing != null) {
      return widget.existing!.keys.where((k) => !skip.contains(k)).toList();
    }
    return const [];
  }

  @override
  void dispose() {
    for (final c in _controllers.values) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _save() async {
    final l10n = AppLocalizations.of(context);
    if (!_formKey.currentState!.validate()) return;
    final values = <String, dynamic>{};
    for (final entry in _controllers.entries) {
      final text = entry.value.text.trim();
      if (text.isNotEmpty) values[entry.key] = text;
    }
    if (values.isEmpty) {
      setState(() => _error = l10n.cabinetEnterAtLeastOneField);
      return;
    }

    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await workContext.api.upsertCabinetRow(
        cabinetId: widget.cabinetId,
        tableSlug: widget.tableSlug,
        values: values,
        rowId: widget.existing?['id'] as String?,
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
    final isEdit = widget.existing != null;
    return AppScaffold(
      title: Text(isEdit ? l10n.cabinetEditRow : l10n.cabinetAddRow),
      body: ListView(
        padding: const EdgeInsets.all(AppSpacing.lg),
        children: [
          Text(
            widget.tableLabel,
            style: Theme.of(context).textTheme.titleMedium,
          ),
          const SizedBox(height: AppSpacing.sm),
          if (_error != null) AppStatusBanner(severity: AppStatusSeverity.error, message: AppErrors.localize(context, _error!)),
          Form(
            key: _formKey,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                for (final name in _controllers.keys) ...[
                  TextFormField(
                    controller: _controllers[name],
                    decoration: InputDecoration(labelText: name),
                    enabled: !_saving,
                  ),
                  const SizedBox(height: AppSpacing.md),
                ],
                AppButton(
                  label: _saving ? l10n.commonSaving : l10n.cabinetSaveRow,
                  onPressed: _saving ? null : _save,
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
