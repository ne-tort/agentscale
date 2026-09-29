import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_snack_bar.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/features/meta/poll_while.dart';
import 'package:prodavan/features/meta/runtime/module_runtime_scope.dart';
import 'package:prodavan/features/meta/meta_icon.dart';
import 'package:prodavan/features/meta/widgets/column_map_field.dart';
import 'package:prodavan/features/meta/widgets/fields_schema_editor_field.dart';
import 'package:prodavan/features/meta/widgets/prompt_files_editor_field.dart';
import 'package:prodavan/features/meta/widgets/file_upload_field.dart';
import 'package:prodavan/features/meta/widgets/schema_attrs_field.dart';
import 'package:prodavan/features/meta/widgets/markdown_editor_field.dart';
import 'package:prodavan/features/meta/widgets/project_multiselect_field.dart';
import 'package:prodavan/features/meta/widgets/remote_database_picker_field.dart';
import 'package:prodavan/features/meta/widgets/remote_table_picker_field.dart';
import 'package:prodavan/features/meta/widgets/build_slots_field.dart';
import 'package:prodavan/features/meta/widgets/text_editor_nav_field.dart';
import 'package:prodavan/features/meta/widgets/type_ref_picker_field.dart';
import 'package:prodavan/features/meta/meta_label.dart';
import 'package:prodavan/features/meta/workspace_path.dart';
import 'package:prodavan/l10n/app_localizations.dart';

class FormViewInterpreter extends StatefulWidget {
  const FormViewInterpreter({
    super.key,
    required this.manifest,
    required this.view,
    required this.seeds,
    this.rowId,
    this.readOnly = false,
    this.onOpenView,
  });

  final ModuleMetaManifest manifest;
  final Map<String, dynamic> view;
  final dynamic seeds;
  final String? rowId;
  final bool readOnly;
  final void Function(String viewSlug, {String? rowId})? onOpenView;

  @override
  State<FormViewInterpreter> createState() => _FormViewInterpreterState();
}

class _FormViewInterpreterState extends State<FormViewInterpreter> {
  late Map<String, dynamic> _values;
  String? _rowId;
  Listenable? _seedsListenable;
  Timer? _pollTimer;
  DateTime? _pollStartedAt;

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
    _pollTimer?.cancel();
    _pollTimer = null;
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
    _updatePolling();
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
      body['remote_table'],
      body['remote_database'],
      body['remote_user'],
      body['remote_password'],
      body['remote_dsn_has_database'],
      body['remote_dsn_url_has_database'],
      body['remote_dsn_has_user'],
      body['remote_dsn_has_password'],
      body['remote_auth_failed'],
      body['remote_dsn_reachable'],
      body['status'],
      body['row_count'],
      body['columns_json'],
      body['error'],
      body['paused'],
      body['column_map'],
      body['project_ids'],
      body['type_id'],
      body['type_name'],
      body['attrs'],
      body['fields_json'],
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

  /// ui_json.poll_while: reload the row on an interval while [field] keeps
  /// [equals] (e.g. status=indexing) — live indexing progress. Hard 30-minute
  /// cap so a hung status cannot poll forever.
  void _updatePolling() {
    final ui = widget.view['ui_json'];
    final config = parsePollWhile(ui is Map ? ui['poll_while'] : null);
    final active = config != null && _rowId != null && config.matches(_values[config.field]);
    if (!active) {
      _pollTimer?.cancel();
      _pollTimer = null;
      _pollStartedAt = null;
      return;
    }
    _pollStartedAt ??= DateTime.now();
    _pollTimer ??= Timer.periodic(config.interval, (_) => _pollTick());
  }

  Future<void> _pollTick() async {
    if (!mounted) return;
    final started = _pollStartedAt;
    if (started != null &&
        DateTime.now().difference(started) > const Duration(minutes: 30)) {
      _pollTimer?.cancel();
      _pollTimer = null;
      return;
    }
    try {
      final reload = widget.seeds.loadAll;
      if (reload is Future Function()) {
        await reload();
      } else if (reload is Function()) {
        final result = reload();
        if (result is Future) await result;
      }
    } catch (_) {}
  }

  /// «В процессе (x из y)» suffix from the row heartbeat fields.
  String _withIndexProgress(String label) {
    final indexed = _values['indexed_count'];
    if (indexed == null) return label;
    final total = _values['total_rows'];
    final totalText = total == null ? '' : ' из $total';
    return '$label ($indexed$totalText)';
  }

  Future<void> _persist(String name, dynamic value) async {
    var next = value;
    if (name == 'path' && next is String) {
      next = normalizeWorkspaceRelativePath(next);
    }
    // «Имя БД» must be a bare DB name; schema.table belongs in remote_table.
    if (name == 'remote_database' && next is String && next.trim().contains('.')) {
      final raw = next.trim();
      setState(() {
        _values['remote_table'] = raw;
        _values['remote_database'] = '';
        // Field is only visible when DSN has no /dbname, so unlock is invalid.
        _values['remote_dsn_has_database'] = false;
      });
      if (widget.readOnly) return;
      try {
        if (_rowId != null) {
          final t = widget.seeds.patchField(_rowId!, 'remote_table', raw);
          if (t is Future) await t;
          final d = widget.seeds.patchField(_rowId!, 'remote_database', '');
          if (d is Future) await d;
          final flag = widget.seeds.patchField(
            _rowId!,
            'remote_dsn_has_database',
            false,
          );
          if (flag is Future) await flag;
        }
      } catch (e) {
        if (!mounted) return;
        AppErrors.showSnack(context, e);
      }
      return;
    }
    setState(() {
      _values[name] = next;
      if (name == 'remote_database' && next is String && next.trim().isNotEmpty) {
        _values['remote_dsn_has_database'] = true;
      }
      if (name == 'remote_user' && next is String && next.trim().isNotEmpty) {
        _values['remote_dsn_has_user'] = true;
        _values['remote_auth_failed'] = false;
      }
    });
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
        final patch = widget.seeds.patchField(_rowId!, name, next);
        if (patch is Future) await patch;
        if (name == 'remote_database' && next is String && next.trim().isNotEmpty) {
          final flag = widget.seeds.patchField(_rowId!, 'remote_dsn_has_database', true);
          if (flag is Future) await flag;
        }
        if (name == 'remote_user' && next is String && next.trim().isNotEmpty) {
          final flag = widget.seeds.patchField(_rowId!, 'remote_dsn_has_user', true);
          if (flag is Future) await flag;
          final auth = widget.seeds.patchField(_rowId!, 'remote_auth_failed', false);
          if (auth is Future) await auth;
        }
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

  /// Single-row upsert for several fields (one auto-probe, one error).
  Future<void> _persistFields(Map<String, dynamic> fields) async {
    if (fields.isEmpty) return;
    setState(() {
      _values.addAll(fields);
      final db = fields['remote_database'];
      if (db is String && db.trim().isNotEmpty) {
        _values['remote_dsn_has_database'] = true;
      }
      final user = fields['remote_user'];
      if (user is String && user.trim().isNotEmpty) {
        _values['remote_dsn_has_user'] = true;
        _values['remote_auth_failed'] = false;
      }
    });
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
        final body = Map<String, dynamic>.from(widget.seeds.bodyFor(_rowId!));
        body.addAll(fields);
        final upsert = widget.seeds.upsertBody(_rowId!, body);
        if (upsert is Future) await upsert;
      }
      if (!mounted || _rowId == null) return;
      final item = widget.seeds.itemById(_rowId!);
      if (item != null) {
        setState(() {
          _values = Map<String, dynamic>.from(widget.seeds.bodyFor(_rowId!));
        });
      }
    } catch (e) {
      await _reloadRowAfterFailure();
      rethrow;
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
    return _matchVisibleWhen(Map<String, dynamic>.from(when));
  }

  bool _matchVisibleWhen(Map<String, dynamic> when) {
    final any = when['any'];
    if (any is List) {
      for (final part in any) {
        if (part is Map &&
            _matchVisibleWhen(Map<String, dynamic>.from(part))) {
          return true;
        }
      }
      return false;
    }
    final all = when['all'];
    if (all is List) {
      for (final part in all) {
        if (part is! Map) return false;
        if (!_matchVisibleWhen(Map<String, dynamic>.from(part))) return false;
      }
      return true;
    }
    final field = when['field']?.toString();
    if (field == null || field.isEmpty) return true;
    final actual = _values[field];
    final actualText = actual?.toString();
    if (when.containsKey('eq')) {
      final expected = when['eq'];
      if (expected is bool) {
        return (actual == true) == expected;
      }
      return actualText == expected?.toString();
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
    if (widgetKind == 'type_ref_picker') {
      final pickView = fieldCfg?['pick_view']?.toString() ?? 'equipment_types_pick';
      final typeId = value?.toString();
      final titleField = fieldCfg?['title_field']?.toString() ?? 'type_name';
      final typeName = _values[titleField]?.toString();
      final iconName = fieldCfg?['icon']?.toString();
      return TypeRefPickerField(
        label: label,
        typeId: typeId,
        typeName: typeName,
        readOnly: fieldReadOnly,
        emptyStyleWarning: fieldCfg?['empty_style']?.toString() == 'warning',
        emptyLabel: typeRefEmptyLabel(fieldCfg, l10n, locale),
        icon: typeRefIcon(iconName),
        onOpenPick: () {
          final open = widget.onOpenView;
          if (open == null || _rowId == null) return;
          open(pickView, rowId: _rowId);
        },
      );
    }
    if (widgetKind == 'remote_database_picker') {
      if (_rowId == null) {
        return AppNavPreference(
          title: label,
          icon: fieldIcon ?? Icons.storage_outlined,
          enabled: false,
          onTap: () {},
        );
      }
      return RemoteDatabasePickerField(
        label: label,
        databaseName: value?.toString(),
        readOnly: fieldReadOnly,
        emptyStyleWarning: fieldCfg?['empty_style']?.toString() == 'warning',
        emptyLabel: remoteDatabaseEmptyLabel(fieldCfg, l10n, locale),
        listActionId: fieldCfg?['list_action']?.toString() ??
            'list_catalog_remote_databases',
        rowId: _rowId!,
        icon: fieldIcon ?? Icons.storage_outlined,
        onClosed: _reloadRowAfterFailure,
        onSelected: (db) async {
          await _persist(name, db);
          // Changing DB invalidates table selection.
          if ((_values['remote_table']?.toString() ?? '').isNotEmpty) {
            await _persist('remote_table', '');
          }
        },
      );
    }
    if (widgetKind == 'remote_table_picker') {
      if (_rowId == null) {
        return AppNavPreference(
          title: label,
          icon: fieldIcon ?? Icons.table_chart_outlined,
          enabled: false,
          onTap: () {},
        );
      }
      return RemoteTablePickerField(
        label: label,
        tableName: value?.toString(),
        readOnly: fieldReadOnly,
        emptyStyleWarning: fieldCfg?['empty_style']?.toString() == 'warning',
        emptyLabel: remoteTableEmptyLabel(fieldCfg, l10n, locale),
        listActionId:
            fieldCfg?['list_action']?.toString() ?? 'list_catalog_remote_tables',
        rowId: _rowId!,
        icon: fieldIcon ?? Icons.table_chart_outlined,
        onClosed: _reloadRowAfterFailure,
        onSelected: (table) => _persist(name, table),
      );
    }
    if (widgetKind == 'schema_attrs') {
      final typeIdField = fieldCfg?['type_id_field']?.toString() ?? 'type_id';
      final typesTable = fieldCfg?['types_table']?.toString() ?? 'equipment_types';
      final fieldsFrom = fieldCfg?['fields_from']?.toString() ?? 'fields_json';
      final typeId = _values[typeIdField]?.toString() ?? '';
      List<Map<String, dynamic>> fields = const [];
      if (typeId.isNotEmpty) {
        final typeItem = widget.seeds.itemById(typeId);
        final typeBody = typeItem is Map
            ? (typeItem['body'] is Map
                ? Map<String, dynamic>.from(typeItem['body'] as Map)
                : <String, dynamic>{})
            : <String, dynamic>{};
        // Prefer live type row; fall back to table scan if id lookup misses.
        if (typeBody.isEmpty) {
          for (final item in widget.seeds.itemsForTable(typesTable) as List) {
            if (item is Map && item['row_id']?.toString() == typeId) {
              final body = item['body'];
              if (body is Map) {
                fields = parseFieldsJson(body[fieldsFrom]);
              }
              break;
            }
          }
        } else {
          fields = parseFieldsJson(typeBody[fieldsFrom]);
        }
      }
      return SchemaAttrsField(
        fields: fields,
        attrs: parseAttrsMap(value),
        readOnly: fieldReadOnly,
        sectionTitle: schemaAttrsSectionTitle(fieldCfg, l10n, locale),
        onChanged: (attrs) => _persist(name, attrs),
      );
    }
    if (widgetKind == 'build_slots') {
      return BuildSlotsField(
        seeds: widget.seeds,
        slots: parseSlotsMap(value),
        buildKind: _values['build_kind']?.toString() ?? 'pc',
        readOnly: fieldReadOnly,
        typesTable: fieldCfg?['types_table']?.toString() ?? 'equipment_types',
        itemsTable: fieldCfg?['items_table']?.toString() ?? 'equipment_items',
        offersTable: fieldCfg?['offers_table']?.toString() ?? 'found_offers',
        pickView: fieldCfg?['pick_view']?.toString() ?? 'equipment_items_pick',
        sectionTitle: buildSlotsSectionTitle(fieldCfg, l10n, locale),
        emptyLabel: buildSlotsEmptyLabel(fieldCfg, l10n, locale),
        rowId: _rowId,
        onOpenPick: widget.onOpenView,
      );
    }
    if (widgetKind == 'fields_schema_editor') {
      return FieldsSchemaEditorField(
        label: label,
        value: value,
        readOnly: fieldReadOnly,
        onChanged: (fields) => _persist(name, fields),
      );
    }
    if (widgetKind == 'prompt_files_editor') {
      return PromptFilesEditorField(
        value: value,
        readOnly: fieldReadOnly,
        onChanged: (files) => _persist(name, files),
      );
    }
    if (widgetKind == 'project_multiselect') {
      return ProjectMultiselectField(
        label: label,
        value: value,
        readOnly: fieldReadOnly,
        subtitleMode: fieldCfg?['subtitle']?.toString(),
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
    if (widgetKind == 'text_editor') {
      return TextEditorNavField(
        label: label,
        value: value?.toString() ?? '',
        readOnly: fieldReadOnly,
        icon: fieldIcon ?? textEditorIcon(fieldIconName),
        emptyLabel: textEditorEmptyLabel(fieldCfg, l10n, locale),
        onChanged: (v) => _persist(name, v),
      );
    }
    if (widgetKind == 'file_upload') {
      final scope = ModuleRuntimeScope.maybeOf(context);
      if (scope == null) {
        return ListTile(
          title: Text(label),
          subtitle: const Text('Upload unavailable (no module scope)'),
        );
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
        scope: scope,
        readOnly: fieldReadOnly || indexing,
        accept: accept,
        subtitle: indexing ? _withIndexProgress('Индексация…') : subtitle,
        warnWhenEmpty: warnWhenEmpty,
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
      final invert = fieldCfg?['invert'] == true;
      final paused = invert ? value != true : value == true;
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
        onTap: () => _persist(name, invert ? paused : !paused),
      );
    }
    if (widgetKind == 'value' && type == 'enum') {
      final raw = value?.toString() ?? '';
      final text = raw.isEmpty ? '' : _enumLabel(column, raw);
      final accent = _fieldAccent(context, fieldCfg, value: raw);
      final indexing = raw == 'indexing';
      final valueText = indexing ? _withIndexProgress(text) : text;
      return AppValuePreference<String>(
        title: label,
        icon: fieldIcon,
        value: valueText,
        enabled: false,
        accentColor: accent,
        busy: indexing,
        trailing: _trailingAction(context, fieldCfg, busy: indexing),
        onSave: (_) async {},
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
        final maxLinesRaw = fieldCfg?['max_lines'];
        final maxLines = maxLinesRaw is num
            ? maxLinesRaw.toInt()
            : int.tryParse(maxLinesRaw?.toString() ?? '') ?? 1;
        final asSecret = type == 'secret_ref' ||
            fieldCfg?['secret'] == true ||
            fieldCfg?['obscure'] == true;
        if (asSecret) {
          return _buildSecretValueField(
            name: name,
            label: label,
            fieldIcon: fieldIcon,
            value: value,
            fieldReadOnly: fieldReadOnly,
            columnType: type,
            hintText: fieldCfg?['hint']?.toString(),
          );
        }
        final accent = _fieldAccent(context, fieldCfg, value: value);
        final copyOnTap = fieldCfg?['copy_on_tap'] == true;
        final text = value?.toString() ?? '';
        return AppValuePreference<String>(
          title: label,
          icon: fieldIcon,
          value: text,
          enabled: !fieldReadOnly && !copyOnTap,
          accentColor: accent,
          maxLines: maxLines < 1 ? 1 : maxLines,
          onTap: copyOnTap && text.isNotEmpty
              ? () {
                  Clipboard.setData(ClipboardData(text: text));
                  AppSnackBar.info(context, l10n.containerErrorCopied);
                }
              : null,
          onSave: (v) async {
            if (type == 'number') {
              final trimmed = v.trim();
              if (trimmed.isEmpty) {
                _persist(name, null);
                return;
              }
              final asInt = int.tryParse(trimmed);
              if (asInt != null) {
                _persist(name, asInt);
                return;
              }
              final asDouble = double.tryParse(trimmed);
              _persist(name, asDouble ?? trimmed);
              return;
            }
            _persist(name, v);
          },
        );
    }
  }

  Widget? _trailingAction(
    BuildContext context,
    Map<String, dynamic>? fieldCfg, {
    bool busy = false,
  }) {
    final raw = fieldCfg?['trailing_action'];
    if (raw is! Map) return null;
    final actionId = raw['action_id']?.toString() ?? '';
    if (actionId.isEmpty) return null;
    final scope = ModuleRuntimeScope.maybeOf(context);
    if (scope == null || scope.invokeActionFn == null) return null;
    final l10n = AppLocalizations.of(context);
    final locale = Localizations.localeOf(context);
    final tooltip = resolveMetaLabel(raw['tooltip'], l10n, locale: locale);
    final icon = metaIconFromName(
      raw['icon']?.toString(),
      fallback: Icons.refresh,
    );
    return IconButton(
      icon: Icon(icon, size: 20),
      tooltip: tooltip.isNotEmpty ? tooltip : actionId,
      onPressed: busy
          ? null
          : () async {
              final rowId = _rowId;
              if (rowId == null || rowId.isEmpty) return;
              try {
                await scope.invokeAction(actionId: actionId, rowId: rowId);
                await _reloadRowAfterFailure();
                if (!context.mounted) return;
                AppSnackBar.info(
                  context,
                  tooltip.isNotEmpty ? tooltip : actionId,
                );
              } catch (e) {
                if (!context.mounted) return;
                AppErrors.showSnack(context, e);
                await _reloadRowAfterFailure();
              }
            },
    );
  }

  Color? _fieldAccent(
    BuildContext context,
    Map<String, dynamic>? fieldCfg, {
    dynamic value,
  }) {
    if (fieldCfg == null) return null;
    final map = fieldCfg['accent_map'];
    if (map is Map && value != null) {
      final key = value.toString();
      final fromMap = map[key]?.toString();
      if (fromMap != null) {
        return _accentColor(context, fromMap);
      }
    }
    return _accentColor(context, fieldCfg['accent']?.toString());
  }

  Color? _accentColor(BuildContext context, String? accent) {
    if (accent == 'error') return context.appColors.danger;
    if (accent == 'warning') return context.appColors.warning;
    if (accent == 'success') return context.appColors.success;
    return null;
  }

  bool _postgresDsnHasDatabase(String dsn) {
    final uri = Uri.tryParse(dsn.trim());
    if (uri == null) return false;
    final path = uri.path.replaceFirst(RegExp(r'^/+'), '');
    if (path.isEmpty) return false;
    final name = path.split('/').first;
    // schema.table in path is not a database name.
    if (name.contains('.')) return false;
    return name.isNotEmpty;
  }

  bool _postgresDsnHasUser(String dsn) {
    final uri = Uri.tryParse(dsn.trim());
    if (uri == null) return false;
    final info = uri.userInfo;
    if (info.isEmpty) return false;
    return info.split(':').first.isNotEmpty;
  }

  bool _postgresDsnHasPassword(String dsn) {
    final uri = Uri.tryParse(dsn.trim());
    if (uri == null) return false;
    final info = uri.userInfo;
    final colon = info.indexOf(':');
    return colon >= 0 && colon < info.length - 1;
  }

  Widget _buildSecretValueField({
    required String name,
    required String label,
    required IconData? fieldIcon,
    required dynamic value,
    required bool fieldReadOnly,
    required String? columnType,
    String? hintText,
  }) {
    final scope = ModuleRuntimeScope.maybeOf(context);
    final hasRef = value is Map &&
        ((value['secret_ref'] as String?)?.trim().isNotEmpty ?? false);
    // Core AppValuePreference: same tile as URL/login, obscure mode.
    return AppValuePreference<String>(
      title: label,
      icon: fieldIcon,
      value: hasRef ? '••••••••' : '',
      enabled: !fieldReadOnly,
      obscureText: true,
      formatInputValue: (_) => '',
      hintText: hintText ?? (hasRef ? '••••••••' : null),
      onSave: (raw) async {
        final secret = raw.trim();
        if (secret.isEmpty) return;
        if (columnType == 'secret_ref') {
          if (scope == null) {
            throw StateError('secret_ref requires module runtime scope');
          }
          final ref = await scope.uploadSecret(secret: secret, label: label);
          if (name == 'remote_dsn') {
            final hasDb = _postgresDsnHasDatabase(secret);
            // One upsert: flags + new DSN together → single auto-probe on the new host.
            await _persistFields({
              'remote_dsn_reachable': false,
              'remote_auth_failed': false,
              'remote_dsn_url_has_database': hasDb,
              'remote_dsn_has_database': hasDb,
              'remote_dsn_has_user': _postgresDsnHasUser(secret),
              'remote_dsn_has_password': _postgresDsnHasPassword(secret),
              'remote_database': '',
              'remote_table': '',
              'remote_dsn': ref,
            });
            return;
          }
          if (name == 'remote_password') {
            await _persistFields({
              'remote_dsn_has_password': true,
              'remote_auth_failed': false,
              'remote_password': ref,
            });
            return;
          }
          await _persist(name, ref);
          return;
        }
        await _persist(name, secret);
      },
    );
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
