import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/features/meta/runtime/module_runtime_scope.dart';
import 'package:prodavan/features/meta/meta_icon.dart';
import 'package:prodavan/features/meta/widgets/column_map_field.dart';
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
  Listenable? _seedsListenable;

  @override
  void initState() {
    super.initState();
    _attachSeedsListener();
    _syncFromSeeds();
  }

  @override
  void didUpdateWidget(covariant FormViewInterpreter oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.seeds != widget.seeds) {
      _detachSeedsListener();
      _attachSeedsListener();
    }
    if (oldWidget.rowId != widget.rowId || oldWidget.seeds != widget.seeds) {
      _syncFromSeeds();
      return;
    }
    // Parent ListenableBuilder rebuilds with same rowId after row body changes
    // (e.g. content.index_tabular sets status=ready). Keep local form state in sync.
    _syncFromSeedsIfChanged();
  }

  @override
  void dispose() {
    _detachSeedsListener();
    super.dispose();
  }

  void _attachSeedsListener() {
    final seeds = widget.seeds;
    if (seeds is Listenable) {
      _seedsListenable = seeds;
      seeds.addListener(_onSeedsChanged);
    }
  }

  void _detachSeedsListener() {
    _seedsListenable?.removeListener(_onSeedsChanged);
    _seedsListenable = null;
  }

  void _onSeedsChanged() {
    if (!mounted) return;
    _syncFromSeedsIfChanged();
  }

  void _syncFromSeedsIfChanged() {
    if (_rowId == null) return;
    final item = widget.seeds.itemById(_rowId!);
    if (item == null) return;
    final next = Map<String, dynamic>.from(widget.seeds.bodyFor(_rowId!));
    if (_bodyFingerprint(next) == _bodyFingerprint(_values)) return;
    setState(() => _values = next);
  }

  String _bodyFingerprint(Map<String, dynamic> body) {
    // Status/columns drive visible_when + column_map; include file identity.
    final file = body['source_file'];
    final fileKey = file is Map ? '${file['storage_key']}|${file['asset_id']}' : '$file';
    return [
      body['status'],
      body['row_count'],
      body['columns_json'],
      body['error'],
      body['paused'],
      body['column_map'],
      body['project_ids'],
      fileKey,
    ].join('¦');
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

  Future<void> _persist(String name, dynamic value) async {
    setState(() => _values[name] = value);
    if (widget.readOnly) return;
    final tableSlug = widget.view['table_slug'] as String? ?? '';
    try {
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
        final created = widget.seeds.createRow(tableSlug);
        _rowId = created is Future ? await created as String : created as String;
        final body = widget.seeds.bodyFor(_rowId!);
        body.addAll(_values);
        final upsert = widget.seeds.upsertBody(_rowId!, body);
        if (upsert is Future) await upsert;
      } else {
        final patch = widget.seeds.patchField(_rowId!, name, value);
        if (patch is Future) await patch;
      }
      if (!mounted || _rowId == null) return;
      final item = widget.seeds.itemById(_rowId!);
      if (item != null) {
        setState(() {
          _values = Map<String, dynamic>.from(widget.seeds.bodyFor(_rowId!));
        });
      }
    } catch (e) {
      // Row may already be committed with status=error/ready from index_tabular.
      await _reloadRowAfterFailure();
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  Future<void> _reloadRowAfterFailure() async {
    if (_rowId == null) return;
    try {
      final reload = widget.seeds.loadAll;
      if (reload is Future Function()) {
        await reload();
      } else if (reload is Function()) {
        final result = reload();
        if (result is Future) await result;
      }
    } catch (_) {}
    if (!mounted || _rowId == null) return;
    if (widget.seeds.itemById(_rowId!) != null) {
      setState(() {
        _values = Map<String, dynamic>.from(widget.seeds.bodyFor(_rowId!));
      });
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
    final fieldEntries = fields is List
        ? fields.whereType<Map>().map((f) => Map<String, dynamic>.from(f)).toList()
        : <Map<String, dynamic>>[];
    final fieldNames = fieldEntries.isNotEmpty
        ? fieldEntries
            .map((f) => f['column'] as String? ?? '')
            .where((n) => n.isNotEmpty)
            .where((n) => _isFieldVisible(_fieldConfig(fieldEntries, n)))
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

  Map<String, dynamic>? _fieldConfig(List<Map<String, dynamic>> fields, String name) {
    for (final f in fields) {
      if (f['column'] == name) return f;
    }
    return null;
  }

  bool _isFieldVisible(Map<String, dynamic>? fieldCfg) {
    if (fieldCfg == null) return true;
    final when = fieldCfg['visible_when'];
    if (when is! Map) return true;
    final field = when['field']?.toString();
    if (field == null || field.isEmpty) return true;
    final actual = _values[field];
    final actualText = actual?.toString();
    if (when.containsKey('eq')) {
      return actualText == when['eq']?.toString();
    }
    if (when['in'] is List) {
      final allowed = (when['in'] as List).map((e) => e.toString()).toSet();
      return actualText != null && allowed.contains(actualText);
    }
    if (when['not_empty'] == true) {
      if (actual == null) return false;
      if (actual is String) return actual.trim().isNotEmpty;
      if (actual is Iterable) return actual.isNotEmpty;
      return true;
    }
    return true;
  }

  Widget _field(BuildContext context, Map<String, dynamic> column, String name, Map<String, dynamic> uiJson) {
    final l10n = AppLocalizations.of(context);
    final locale = Localizations.localeOf(context);
    final label = resolveMetaLabel(column['label'] ?? name, l10n, locale: locale);
    final type = column['type'] as String? ?? 'text';
    final value = _values[name];
    final fields = uiJson['fields'];
    final fieldEntries = fields is List
        ? fields.whereType<Map>().map((f) => Map<String, dynamic>.from(f)).toList()
        : <Map<String, dynamic>>[];
    final fieldCfg = _fieldConfig(fieldEntries, name);
    final widgetKind = fieldCfg?['widget'] as String?;
    final fieldReadOnly = widget.readOnly || fieldCfg?['read_only'] == true;
    final fieldIconName = fieldCfg?['icon'] as String?;
    final fieldIcon =
        fieldIconName != null && fieldIconName.isNotEmpty ? metaIconFromName(fieldIconName) : null;

    if (widgetKind == 'column_map') {
      final sourceFrom = fieldCfg?['source_columns_from']?.toString() ?? 'columns_json';
      final schemaRaw = fieldCfg?['schema'];
      final schema = schemaRaw is List
          ? schemaRaw.whereType<Map>().map((e) => Map<String, dynamic>.from(e)).toList()
          : <Map<String, dynamic>>[];
      return ColumnMapField(
        label: label,
        value: value,
        sourceColumns: parseColumnsJson(_values[sourceFrom]),
        schema: schema,
        readOnly: fieldReadOnly,
        onChanged: (map) => _persist(name, map),
      );
    }
    if (widgetKind == 'project_multiselect') {
      return ProjectMultiselectField(
        label: label,
        value: value,
        readOnly: fieldReadOnly,
        onChanged: (ids) => _persist(name, ids),
      );
    }
    if (widgetKind == 'markdown_editor') {
      return MarkdownEditorField(
        label: label,
        value: value?.toString() ?? '',
        readOnly: fieldReadOnly,
        onChanged: (v) => _persist(name, v),
        onUploadMarkdown: fieldReadOnly
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
      final accept = fieldCfg?['accept'] as String?;
      final warnWhenEmpty = fieldCfg?['empty_style']?.toString() == 'warning';
      String? subtitle;
      final subtitleFrom = fieldCfg?['subtitle_from'];
      if (subtitleFrom is String && subtitleFrom.isNotEmpty) {
        final raw = _values[subtitleFrom];
        if (raw != null) {
          final text = raw.toString().trim();
          if (text.isNotEmpty && text != '0') {
            subtitle = subtitleFrom == 'row_count' ? '$text строк' : text;
          }
        }
      }
      final status = _values['status']?.toString();
      final indexing = status == 'indexing';
      return FileUploadField(
        label: label,
        value: value,
        cabinetId: scope.cabinetId,
        api: scope.api,
        readOnly: fieldReadOnly || indexing,
        accept: accept,
        subtitle: indexing
            ? 'Индексация…'
            : subtitle,
        warnWhenEmpty: warnWhenEmpty,
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
        readOnly: fieldReadOnly,
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
        icon: fieldIcon,
        value: choices.contains(current) ? current : '',
        choices: choices,
        keyFor: (v) => v,
        labelFor: (v) => labels[v] ?? v,
        enabled: !fieldReadOnly,
        onSave: (v) async {
          _persist(name, v.isEmpty ? null : v);
        },
      );
    }
    if (widgetKind == 'pause_toggle') {
      final paused = value == true;
      final warning = context.appColors.warning;
      final actionLabel = resolveMetaLabel(
        paused
            ? (fieldCfg?['resume_label'] ?? {'ru': 'Возобновить', 'en': 'Resume'})
            : (fieldCfg?['pause_label'] ?? {'ru': 'Приостановить', 'en': 'Pause'}),
        l10n,
        locale: Localizations.localeOf(context),
      );
      final iconName = paused
          ? (fieldCfg?['resume_icon']?.toString() ?? 'play_arrow_outlined')
          : (fieldCfg?['pause_icon']?.toString() ?? 'pause_outlined');
      return AppNavPreference(
        title: actionLabel,
        icon: metaIconFromName(iconName),
        accentColor: warning,
        enabled: !fieldReadOnly,
        onTap: () => _persist(name, !paused),
      );
    }
    if (widgetKind == 'switch' || type == 'bool') {
      return AppSwitchPreference(
        title: label,
        icon: fieldIcon,
        value: value == true,
        enabled: !fieldReadOnly,
        onChanged: (v) async {
          _persist(name, v);
        },
      );
    }
    if (widgetKind == 'choice' || type == 'enum') {
      final choices = _enumChoices(column);
      final current = value?.toString() ?? (choices.isNotEmpty ? choices.first : '');
      return AppChoicePreference<String>(
        title: label,
        icon: fieldIcon,
        value: current,
        choices: choices,
        keyFor: (v) => v,
        labelFor: (v) => _enumLabel(column, v),
        enabled: !fieldReadOnly,
        onSave: (v) async {
          _persist(name, v);
        },
      );
    }

    switch (type) {
      case 'enum':
        final choices = _enumChoices(column);
        final current = value?.toString() ?? (choices.isNotEmpty ? choices.first : '');
        return AppChoicePreference<String>(
          title: label,
          icon: fieldIcon,
          value: current,
          choices: choices,
          keyFor: (v) => v,
          labelFor: (v) => _enumLabel(column, v),
          enabled: !fieldReadOnly,
          onSave: (v) async {
            _persist(name, v);
          },
        );
      default:
        return AppValuePreference<String>(
          title: label,
          icon: fieldIcon,
          value: value?.toString() ?? '',
          enabled: !fieldReadOnly,
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
