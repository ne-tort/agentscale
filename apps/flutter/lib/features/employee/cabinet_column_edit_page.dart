import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_form.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/danger_confirm_page.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';

/// Edit column metadata / type (L06 PATCH columns).
class CabinetColumnEditPage extends StatefulWidget {
  const CabinetColumnEditPage({
    super.key,
    required this.cabinetId,
    required this.tableSlug,
    required this.tableLabel,
    required this.column,
  });

  final String cabinetId;
  final String tableSlug;
  final String tableLabel;
  final Map<String, dynamic> column;

  @override
  State<CabinetColumnEditPage> createState() => _CabinetColumnEditPageState();
}

class _CabinetColumnEditPageState extends State<CabinetColumnEditPage> {
  static const _columnTypes = ['text', 'number', 'bool', 'datetime', 'json', 'enum', 'ref', 'file_ref'];
  static const _protectedNames = {'id', 'created_at'};

  late String _type;
  late bool _required;
  late bool _unique;
  bool _saving = false;
  bool _deleting = false;
  String? _error;

  String get _columnName => widget.column['name'] as String? ?? '';

  bool get _isProtected => _protectedNames.contains(_columnName);

  @override
  void initState() {
    super.initState();
    _type = widget.column['type'] as String? ?? 'text';
    _required = widget.column['required'] as bool? ?? false;
    _unique = widget.column['unique'] as bool? ?? false;
  }

  Future<void> _save() async {
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await workContext.api.updateMetaColumn(
        cabinetId: widget.cabinetId,
        tableSlug: widget.tableSlug,
        columnName: _columnName,
        type: _type,
        required: _required,
        unique: _unique,
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

  Future<void> _delete() async {
    final ok = await DangerConfirmPage.push(
      context,
      title: 'Delete column?',
      message: 'Remove column "$_columnName" and its data.',
      confirmLabel: 'Delete',
    );
    if (ok != true) return;

    setState(() {
      _deleting = true;
      _error = null;
    });
    try {
      await workContext.api.deleteMetaColumn(
        cabinetId: widget.cabinetId,
        tableSlug: widget.tableSlug,
        columnName: _columnName,
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
    final busy = _saving || _deleting;
    return AppScaffold(
      title: const Text('Column settings'),
      body: ListView(
        padding: const EdgeInsets.all(AppSpacing.lg),
        children: [
          Text(widget.tableLabel, style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: AppSpacing.sm),
          Text(_columnName, style: Theme.of(context).textTheme.titleSmall),
          if (_isProtected)
            Padding(
              padding: const EdgeInsets.only(top: AppSpacing.sm),
              child: Text(
                'System column — read only.',
                style: Theme.of(context).textTheme.bodySmall?.copyWith(
                      color: Theme.of(context).colorScheme.onSurfaceVariant,
                    ),
              ),
            ),
          if (_error != null) ...[
            const SizedBox(height: AppSpacing.sm),
            InlineErrorBanner(message: _error!),
          ],
          if (!_isProtected) ...[
            const SizedBox(height: AppSpacing.md),
            AppForm(
              formKey: GlobalKey<FormState>(),
              children: [
                DropdownButtonFormField<String>(
                  value: _type,
                  decoration: const InputDecoration(labelText: 'Type'),
                  items: _columnTypes
                      .map((t) => DropdownMenuItem(value: t, child: Text(t)))
                      .toList(),
                  onChanged: busy ? null : (v) => setState(() => _type = v ?? _type),
                ),
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('Required'),
                  value: _required,
                  onChanged: busy ? null : (v) => setState(() => _required = v),
                ),
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('Unique'),
                  value: _unique,
                  onChanged: busy ? null : (v) => setState(() => _unique = v),
                ),
                AppButton(
                  label: _saving ? 'Saving…' : 'Save',
                  onPressed: busy ? null : _save,
                ),
              ],
            ),
            const Divider(height: 32),
            AppButton(
              label: _deleting ? 'Deleting…' : 'Delete column',
              onPressed: busy ? null : _delete,
            ),
          ],
        ],
      ),
    );
  }
}
