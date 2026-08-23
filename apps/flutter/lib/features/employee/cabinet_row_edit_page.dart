import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_form.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_text_field.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';

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
  String? _error;

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
    if (!_formKey.currentState!.validate()) return;
    final values = <String, dynamic>{};
    for (final entry in _controllers.entries) {
      final text = entry.value.text.trim();
      if (text.isNotEmpty) values[entry.key] = text;
    }
    if (values.isEmpty) {
      setState(() => _error = 'Enter at least one field value');
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
        _error = e.toString();
        _saving = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final isEdit = widget.existing != null;
    return AppScaffold(
      title: Text(isEdit ? 'Edit row' : 'Add row'),
      body: ListView(
        padding: const EdgeInsets.all(AppSpacing.lg),
        children: [
          Text(
            widget.tableLabel,
            style: Theme.of(context).textTheme.titleMedium,
          ),
          const SizedBox(height: AppSpacing.sm),
          if (_error != null) InlineErrorBanner(message: _error!),
          AppForm(
            formKey: _formKey,
            children: [
              for (final name in _controllers.keys)
                AppTextField(
                  controller: _controllers[name],
                  label: name,
                  enabled: !_saving,
                ),
              AppButton(
                label: _saving ? 'Saving…' : 'Save row',
                onPressed: _saving ? null : _save,
              ),
            ],
          ),
        ],
      ),
    );
  }
}
