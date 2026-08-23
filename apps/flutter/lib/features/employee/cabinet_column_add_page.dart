import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_form.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_text_field.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';

/// Add column to existing meta table (L06 mutate API).
class CabinetColumnAddPage extends StatefulWidget {
  const CabinetColumnAddPage({
    super.key,
    required this.cabinetId,
    required this.tableSlug,
    required this.tableLabel,
  });

  final String cabinetId;
  final String tableSlug;
  final String tableLabel;

  @override
  State<CabinetColumnAddPage> createState() => _CabinetColumnAddPageState();
}

class _CabinetColumnAddPageState extends State<CabinetColumnAddPage> {
  static const _columnTypes = ['text', 'number', 'bool', 'datetime', 'json', 'enum', 'ref', 'file_ref'];

  final _formKey = GlobalKey<FormState>();
  final _name = TextEditingController();
  String _type = 'text';
  bool _required = false;
  bool _saving = false;
  String? _error;

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    if (!_formKey.currentState!.validate()) return;

    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await workContext.api.addMetaColumn(
        cabinetId: widget.cabinetId,
        tableSlug: widget.tableSlug,
        name: _name.text.trim(),
        type: _type,
        required: _required,
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
      title: const Text('Add column'),
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
              AppTextField(
                controller: _name,
                label: 'Column name',
                enabled: !_saving,
                validator: (v) {
                  final s = (v ?? '').trim();
                  if (s.isEmpty) return 'Required';
                  if (!RegExp(r'^[a-zA-Z_][a-zA-Z0-9_]*$').hasMatch(s)) {
                    return 'Letters, digits, underscore';
                  }
                  if (s == 'id' || s == 'created_at') return 'Reserved name';
                  return null;
                },
              ),
              DropdownButtonFormField<String>(
                value: _type,
                decoration: const InputDecoration(labelText: 'Type'),
                items: [
                  for (final t in _columnTypes) DropdownMenuItem(value: t, child: Text(t)),
                ],
                onChanged: _saving ? null : (v) => setState(() => _type = v ?? 'text'),
              ),
              CheckboxListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text('Required'),
                value: _required,
                onChanged: _saving ? null : (v) => setState(() => _required = v ?? false),
                controlAffinity: ListTileControlAffinity.leading,
              ),
              AppButton(
                label: _saving ? 'Adding…' : 'Add column',
                onPressed: _saving ? null : _save,
              ),
            ],
          ),
        ],
      ),
    );
  }
}
