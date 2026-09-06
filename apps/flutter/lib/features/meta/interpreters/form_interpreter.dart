import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/features/meta/runtime/module_runtime_scope.dart';
import 'package:prodavan/features/meta/widgets/file_upload_field.dart';
import 'package:prodavan/features/meta/widgets/secret_upload_field.dart';
import 'package:prodavan/features/meta/widgets/markdown_editor_field.dart';
import 'package:prodavan/features/meta/widgets/project_multiselect_field.dart';
import 'package:prodavan/features/meta/meta_label.dart';
import 'package:prodavan/l10n/app_localizations.dart';

class FormViewInterpreter extends StatefulWidget {
  const FormViewInterpreter({
    super.key,
    required this.manifest,
    required this.view,
    required this.seeds,
    this.rowId,
    this.readOnly = false,
  });

  final ModuleMetaManifest manifest;
  final Map<String, dynamic> view;
  final dynamic seeds;
  final String? rowId;
  final bool readOnly;

  @override
  State<FormViewInterpreter> createState() => _FormViewInterpreterState();
}

class _FormViewInterpreterState extends State<FormViewInterpreter> {
  late Map<String, dynamic> _values;
  String? _rowId;

  @override
  void initState() {
    super.initState();
    _syncFromSeeds();
  }

  @override
  void didUpdateWidget(covariant FormViewInterpreter oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.rowId != widget.rowId) {
      _syncFromSeeds();
    }
  }

  void _syncFromSeeds() {
    final tableSlug = widget.view['table_slug'] as String? ?? '';
    _rowId = widget.rowId;
    if (_rowId != null && widget.seeds.itemById(_rowId!) != null) {
      _values = Map<String, dynamic>.from(widget.seeds.bodyFor(_rowId!));
      return;
    }
    if (_rowId == null || widget.seeds.itemById(_rowId!) == null) {
      final items = widget.seeds.itemsForTable(tableSlug);
      _rowId = items.isNotEmpty ? items.first['row_id'] as String? : null;
    }
    if (_rowId != null) {
      _values = Map<String, dynamic>.from(widget.seeds.bodyFor(_rowId!));
    } else {
      _values = Map<String, dynamic>.from(widget.seeds.defaultBodyForTable(tableSlug));
    }
  }

  void _persist(String name, dynamic value) {
    setState(() => _values[name] = value);
    if (widget.readOnly) return;
    final tableSlug = widget.view['table_slug'] as String? ?? '';
    if (_rowId == null) {
      final ui = widget.view['ui_json'];
      final hidden = ui is Map ? ui['hidden_defaults'] : null;
      if (hidden is Map) {
        _values.addAll(Map<String, dynamic>.from(hidden));
      }
      if (widget.rowId != null) {
        final profileField = widget.view['table_slug'] == 'agents_md' ? 'profile_id' : null;
        if (profileField != null) {
          _values[profileField] = widget.rowId;
        }
      }
      _rowId = widget.seeds.createRow(tableSlug);
      final body = widget.seeds.bodyFor(_rowId!);
      body.addAll(_values);
      widget.seeds.upsertBody(_rowId!, body);
    } else {
      widget.seeds.patchField(_rowId!, name, value);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final ui = widget.view['ui_json'];
    if (ui is! Map) {
      return EmptyPlaceholder(title: l10n.adminMetaInvalid);
    }
    final uiJson = Map<String, dynamic>.from(ui);
    final tableSlug = widget.view['table_slug'] as String? ?? '';
    final columns = widget.manifest.columnsForTable(tableSlug);
    final colByName = {for (final c in columns) c['name'] as String: c};

    final fields = uiJson['fields'];
    final fieldNames = fields is List
        ? fields
            .whereType<Map>()
            .map((f) => f['column'] as String? ?? '')
            .where((n) => n.isNotEmpty)
            .toList()
        : columns.map((c) => c['name'] as String).toList();

    if (_rowId == null && fieldNames.isEmpty) {
      return EmptyPlaceholder(title: l10n.adminModuleSeedEmpty);
    }

    return ListView(
      padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
      children: [
        for (final name in fieldNames)
          if (colByName[name] != null) _field(context, colByName[name]!, name, uiJson),
      ],
    );
  }

  Widget _field(BuildContext context, Map<String, dynamic> column, String name, Map<String, dynamic> uiJson) {
    final l10n = AppLocalizations.of(context);
    final locale = Localizations.localeOf(context);
    final label = resolveMetaLabel(column['label'] ?? name, l10n, locale: locale);
    final type = column['type'] as String? ?? 'text';
    final value = _values[name];
    final fields = uiJson['fields'];
    String? widgetKind;
    if (fields is List) {
      for (final f in fields.whereType<Map>()) {
        if (f['column'] == name) {
          widgetKind = f['widget'] as String?;
          break;
        }
      }
    }

    if (widgetKind == 'project_multiselect') {
      return ProjectMultiselectField(
        label: label,
        value: value,
        readOnly: widget.readOnly,
        onChanged: (ids) => _persist(name, ids),
      );
    }
    if (widgetKind == 'markdown_editor') {
      return MarkdownEditorField(
        label: label,
        value: value?.toString() ?? '',
        readOnly: widget.readOnly,
        onChanged: (v) => _persist(name, v),
        onUploadMarkdown: widget.readOnly
            ? null
            : (text) async {
                final picked = await pickMarkdownFileText();
                if (picked != null) {
                  _persist(name, picked);
                }
              },
      );
    }
    if (widgetKind == 'file_upload') {
      final scope = ModuleRuntimeScope.maybeOf(context);
      if (scope == null) {
        return ListTile(title: Text(label), subtitle: const Text('file (preview only)'));
      }
      String? accept;
      if (fields is List) {
        for (final f in fields.whereType<Map>()) {
          if (f['column'] == name && f['accept'] is String) {
            accept = f['accept'] as String;
            break;
          }
        }
      }
      return FileUploadField(
        label: label,
        value: value,
        cabinetId: scope.cabinetId,
        api: scope.api,
        readOnly: widget.readOnly,
        accept: accept,
        onChanged: (ref) => _persist(name, ref),
      );
    }
    if (widgetKind == 'secret_upload' || type == 'secret_ref') {
      final scope = ModuleRuntimeScope.maybeOf(context);
      if (scope == null) {
        return ListTile(title: Text(label), subtitle: const Text('secret (preview only)'));
      }
      return SecretUploadField(
        label: label,
        value: value,
        cabinetId: scope.cabinetId,
        moduleId: scope.moduleId,
        api: scope.api,
        readOnly: widget.readOnly,
        onChanged: (ref) => _persist(name, ref),
      );
    }
    if (widgetKind == 'ref' || type == 'ref') {
      final refMeta = column['ref'];
      final refTable = refMeta is Map ? refMeta['table_slug'] as String? : null;
      final choices = <String>[''];
      final labels = <String, String>{'': '—'};
      if (refTable != null && refTable.isNotEmpty) {
        for (final item in widget.seeds.itemsForTable(refTable)) {
          final id = item['row_id']?.toString() ?? '';
          if (id.isEmpty) continue;
          final body = item['body'];
          final title = body is Map
              ? (body['name'] ?? body['title'] ?? id).toString()
              : id;
          choices.add(id);
          labels[id] = title;
        }
      }
      final current = value?.toString() ?? '';
      return AppChoicePreference<String>(
        title: label,
        value: choices.contains(current) ? current : '',
        choices: choices,
        keyFor: (v) => v,
        labelFor: (v) => labels[v] ?? v,
        enabled: !widget.readOnly,
        onSave: (v) async {
          _persist(name, v.isEmpty ? null : v);
        },
      );
    }
    if (widgetKind == 'choice' || type == 'enum') {
      final choices = _enumChoices(column);
      final current = value?.toString() ?? (choices.isNotEmpty ? choices.first : '');
      return AppChoicePreference<String>(
        title: label,
        value: current,
        choices: choices,
        keyFor: (v) => v,
        labelFor: (v) => _enumLabel(column, v),
        enabled: !widget.readOnly,
        onSave: (v) async {
          _persist(name, v);
        },
      );
    }

    switch (type) {
      case 'bool':
        return AppSwitchPreference(
          title: label,
          value: value == true,
          enabled: !widget.readOnly,
          onChanged: (v) async {
            _persist(name, v);
          },
        );
      case 'enum':
        final choices = _enumChoices(column);
        final current = value?.toString() ?? (choices.isNotEmpty ? choices.first : '');
        return AppChoicePreference<String>(
          title: label,
          value: current,
          choices: choices,
          keyFor: (v) => v,
          labelFor: (v) => _enumLabel(column, v),
          enabled: !widget.readOnly,
          onSave: (v) async {
            _persist(name, v);
          },
        );
      default:
        return AppValuePreference<String>(
          title: label,
          value: value?.toString() ?? '',
          enabled: !widget.readOnly,
          onSave: (v) async {
            _persist(name, v);
          },
        );
    }
  }

  List<String> _enumChoices(Map<String, dynamic> column) {
    final en = column['enum'];
    if (en is Map && en['values'] is List) {
      return (en['values'] as List).map((e) => e.toString()).toList();
    }
    return const [];
  }

  String _enumLabel(Map<String, dynamic> column, String value) {
    final en = column['enum'];
    if (en is Map && en['labels'] is Map) {
      final labels = Map<String, dynamic>.from(en['labels'] as Map);
      if (labels.containsKey(value)) return labels[value].toString();
    }
    return value;
  }
}
