import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_form.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_text_field.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';

/// Create meta table with initial columns (L06 mutate API).
class CabinetTableCreatePage extends StatefulWidget {
  const CabinetTableCreatePage({super.key, required this.cabinetId});

  final String cabinetId;

  @override
  State<CabinetTableCreatePage> createState() => _CabinetTableCreatePageState();
}

class _ColumnDraft {
  _ColumnDraft({String initialName = '', this.type = 'text', this.required = false})
      : name = TextEditingController(text: initialName);

  final TextEditingController name;
  String type;
  bool required;
}

class _CabinetTableCreatePageState extends State<CabinetTableCreatePage> {
  static const _columnTypes = ['text', 'number', 'bool', 'datetime', 'json', 'enum', 'ref', 'file_ref'];

  final _formKey = GlobalKey<FormState>();
  final _slug = TextEditingController();
  final _label = TextEditingController();
  final _columns = [_ColumnDraft(initialName: 'name', type: 'text', required: true)];
  String _storageKind = 'physical';
  bool _saving = false;
  String? _error;

  @override
  void dispose() {
    _slug.dispose();
    _label.dispose();
    for (final col in _columns) {
      col.name.dispose();
    }
    super.dispose();
  }

  void _addColumn() {
    setState(() => _columns.add(_ColumnDraft()));
  }

  void _removeColumn(int index) {
    if (_columns.length <= 1) return;
    final removed = _columns.removeAt(index);
    removed.name.dispose();
    setState(() {});
  }

  Future<void> _save() async {
    if (!_formKey.currentState!.validate()) return;

    final slug = _slug.text.trim();
    final label = _label.text.trim();
    final columns = <Map<String, dynamic>>[];
    for (final col in _columns) {
      final name = col.name.text.trim();
      if (name.isEmpty) continue;
      columns.add({
        'name': name,
        'type': col.type,
        'required': col.required,
      });
    }
    if (columns.isEmpty) {
      setState(() => _error = 'Add at least one column');
      return;
    }

    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await workContext.api.createMetaTable(
        cabinetId: widget.cabinetId,
        slug: slug,
        label: label,
        columns: columns,
        storageKind: _storageKind,
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
    return AppScaffold(
      title: const Text('New table'),
      body: ListView(
        padding: const EdgeInsets.all(AppSpacing.lg),
        children: [
          if (_error != null) InlineErrorBanner(message: _error!),
          AppForm(
            formKey: _formKey,
            children: [
              AppTextField(
                controller: _slug,
                label: 'Slug',
                enabled: !_saving,
                validator: (v) {
                  final s = (v ?? '').trim();
                  if (s.isEmpty) return 'Required';
                  if (!RegExp(r'^[a-z][a-z0-9_]*$').hasMatch(s)) {
                    return 'Lowercase letters, digits, underscore';
                  }
                  return null;
                },
              ),
              AppTextField(
                controller: _label,
                label: 'Label',
                enabled: !_saving,
                validator: (v) => (v ?? '').trim().isEmpty ? 'Required' : null,
              ),
              DropdownButtonFormField<String>(
                value: _storageKind,
                decoration: const InputDecoration(labelText: 'Storage'),
                items: const [
                  DropdownMenuItem(value: 'physical', child: Text('physical')),
                  DropdownMenuItem(value: 'json_document', child: Text('json_document')),
                ],
                onChanged: _saving ? null : (v) => setState(() => _storageKind = v ?? 'physical'),
              ),
              const SizedBox(height: AppSpacing.md),
              Text('Columns', style: Theme.of(context).textTheme.titleSmall),
              const SizedBox(height: AppSpacing.sm),
              for (var i = 0; i < _columns.length; i++) ...[
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Expanded(
                      flex: 2,
                      child: AppTextField(
                        controller: _columns[i].name,
                        label: 'Column name',
                        enabled: !_saving,
                        validator: (v) => (v ?? '').trim().isEmpty ? 'Required' : null,
                      ),
                    ),
                    const SizedBox(width: AppSpacing.sm),
                    Expanded(
                      child: DropdownButtonFormField<String>(
                        value: _columns[i].type,
                        decoration: const InputDecoration(labelText: 'Type'),
                        items: [
                          for (final t in _columnTypes)
                            DropdownMenuItem(value: t, child: Text(t)),
                        ],
                        onChanged: _saving
                            ? null
                            : (v) {
                                if (v == null) return;
                                setState(() => _columns[i].type = v);
                              },
                      ),
                    ),
                    Checkbox(
                      value: _columns[i].required,
                      onChanged: _saving
                          ? null
                          : (v) => setState(() => _columns[i].required = v ?? false),
                    ),
                    if (_columns.length > 1)
                      IconButton(
                        icon: const Icon(Icons.remove_circle_outline),
                        onPressed: _saving ? null : () => _removeColumn(i),
                      ),
                  ],
                ),
                const SizedBox(height: AppSpacing.sm),
              ],
              Align(
                alignment: Alignment.centerLeft,
                child: TextButton.icon(
                  onPressed: _saving ? null : _addColumn,
                  icon: const Icon(Icons.add),
                  label: const Text('Add column'),
                ),
              ),
              AppButton(
                label: _saving ? 'Creating…' : 'Create table',
                onPressed: _saving ? null : _save,
              ),
            ],
          ),
        ],
      ),
    );
  }
}
