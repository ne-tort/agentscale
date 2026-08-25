import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_form.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_text_field.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';
import 'package:prodavan/l10n/app_localizations.dart';

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
    final l10n = AppLocalizations.of(context);
    return AppScaffold(
      title: Text(l10n.cabinetAddColumn),
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
                label: l10n.cabinetColumnName,
                enabled: !_saving,
                validator: (v) {
                  final s = (v ?? '').trim();
                  if (s.isEmpty) return l10n.commonRequired;
                  if (!RegExp(r'^[a-zA-Z_][a-zA-Z0-9_]*$').hasMatch(s)) {
                    return l10n.cabinetLettersDigitsUnderscore;
                  }
                  if (s == 'id' || s == 'created_at') return l10n.cabinetReservedName;
                  return null;
                },
              ),
              DropdownButtonFormField<String>(
                value: _type,
                decoration: InputDecoration(labelText: l10n.cabinetType),
                items: [
                  for (final t in _columnTypes) DropdownMenuItem(value: t, child: Text(t)),
                ],
                onChanged: _saving ? null : (v) => setState(() => _type = v ?? 'text'),
              ),
              CheckboxListTile(
                contentPadding: EdgeInsets.zero,
                title: Text(l10n.commonRequired),
                value: _required,
                onChanged: _saving ? null : (v) => setState(() => _required = v ?? false),
                controlAffinity: ListTileControlAffinity.leading,
              ),
              AppButton(
                label: _saving ? l10n.commonAdding : l10n.cabinetAddColumn,
                onPressed: _saving ? null : _save,
              ),
            ],
          ),
        ],
      ),
    );
  }
}
