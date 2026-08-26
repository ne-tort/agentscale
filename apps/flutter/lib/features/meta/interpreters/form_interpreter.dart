import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/features/meta/preview/preview_data_store.dart';
import 'package:prodavan/features/meta/preview/preview_stub.dart';
import 'package:prodavan/l10n/app_localizations.dart';

class FormViewInterpreter extends StatefulWidget {
  const FormViewInterpreter({
    super.key,
    required this.manifest,
    required this.view,
    this.rowIndex = 0,
  });

  final ModuleMetaManifest manifest;
  final Map<String, dynamic> view;
  final int rowIndex;

  @override
  State<FormViewInterpreter> createState() => _FormViewInterpreterState();
}

class _FormViewInterpreterState extends State<FormViewInterpreter> {
  late Map<String, dynamic> _values;

  @override
  void initState() {
    super.initState();
    _values = _initialValues();
  }

  Map<String, dynamic> _initialValues() {
    final tableSlug = widget.view['table_slug'] as String? ?? '';
    final rows = PreviewDataStore.rowsForTable(widget.manifest, tableSlug, count: 3);
    if (rows.isEmpty) return {};
    return Map<String, dynamic>.from(rows[widget.rowIndex.clamp(0, rows.length - 1)]);
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

    return ListView(
      padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
      children: [
        for (final name in fieldNames)
          if (colByName[name] != null) _field(context, colByName[name]!, name),
      ],
    );
  }

  Widget _field(BuildContext context, Map<String, dynamic> column, String name) {
    final label = column['label'] as String? ?? name;
    final type = column['type'] as String? ?? 'text';
    final value = _values[name];

    switch (type) {
      case 'bool':
        return AppSwitchPreference(
          title: label,
          value: value == true,
          onChanged: (v) async {
            setState(() => _values[name] = v);
            PreviewStub.run(context, label);
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
          onSave: (v) async {
            setState(() => _values[name] = v);
            PreviewStub.run(context, label);
          },
        );
      default:
        return AppValuePreference<String>(
          title: label,
          value: value?.toString() ?? '',
          onSave: (v) async {
            setState(() => _values[name] = v);
            PreviewStub.run(context, label);
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
