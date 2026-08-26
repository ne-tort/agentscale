import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/l10n/app_localizations.dart';

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
  static const _storageKinds = ['physical', 'json_document'];

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

  String _storageLabel(String kind) {
    final l10n = AppLocalizations.of(context);
    return switch (kind) {
      'json_document' => l10n.cabinetStorageJsonDocument,
      _ => l10n.cabinetStoragePhysical,
    };
  }

  Future<void> _save() async {
    if (!_formKey.currentState!.validate()) return;
    final l10n = AppLocalizations.of(context);

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
      setState(() => _error = l10n.cabinetAddAtLeastOneColumn);
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
    final l10n = AppLocalizations.of(context);
    return AppScaffold(
      title: Text(l10n.cabinetNewTable),
      body: ListView(
        padding: const EdgeInsets.all(AppSpacing.lg),
        children: [
          if (_error != null) AppStatusBanner(severity: AppStatusSeverity.error, message: _error!),
          Form(
            key: _formKey,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                TextFormField(
                  controller: _slug,
                  decoration: InputDecoration(labelText: l10n.cabinetSlug),
                  enabled: !_saving,
                  validator: (v) {
                    final s = (v ?? '').trim();
                    if (s.isEmpty) return l10n.commonRequired;
                    if (!RegExp(r'^[a-z][a-z0-9_]*$').hasMatch(s)) {
                      return l10n.cabinetLowercaseSlugRule;
                    }
                    return null;
                  },
                ),
                const SizedBox(height: AppSpacing.md),
                TextFormField(
                  controller: _label,
                  decoration: InputDecoration(labelText: l10n.commonLabel),
                  enabled: !_saving,
                  validator: (v) => (v ?? '').trim().isEmpty ? l10n.commonRequired : null,
                ),
                const SizedBox(height: AppSpacing.md),
                AppChoicePreference<String>(
                  title: l10n.cabinetStorage,
                  icon: Icons.storage_outlined,
                  value: _storageKind,
                  choices: _storageKinds,
                  keyFor: (v) => v,
                  labelFor: _storageLabel,
                  enabled: !_saving,
                  onSave: (v) async => setState(() => _storageKind = v),
                ),
                const SizedBox(height: AppSpacing.md),
                Text(l10n.cabinetColumns, style: Theme.of(context).textTheme.titleSmall),
                const SizedBox(height: AppSpacing.sm),
                for (var i = 0; i < _columns.length; i++) ...[
                  TextFormField(
                    controller: _columns[i].name,
                    decoration: InputDecoration(labelText: l10n.cabinetColumnName),
                    enabled: !_saving,
                    validator: (v) => (v ?? '').trim().isEmpty ? l10n.commonRequired : null,
                  ),
                  AppChoicePreference<String>(
                    title: l10n.cabinetType,
                    icon: Icons.category_outlined,
                    value: _columns[i].type,
                    choices: _columnTypes,
                    keyFor: (v) => v,
                    labelFor: (v) => v,
                    enabled: !_saving,
                    onSave: (v) async => setState(() => _columns[i].type = v),
                  ),
                  Row(
                    children: [
                      Checkbox(
                        value: _columns[i].required,
                        onChanged: _saving
                            ? null
                            : (v) => setState(() => _columns[i].required = v ?? false),
                      ),
                      Text(l10n.commonRequired),
                      const Spacer(),
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
                    icon: Icon(Icons.add),
                    label: Text(l10n.cabinetAddColumn),
                  ),
                ),
                AppButton(
                  label: _saving ? l10n.commonCreating : l10n.cabinetCreateTable,
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
