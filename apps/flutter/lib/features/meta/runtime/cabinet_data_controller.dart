import 'package:flutter/foundation.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/features/meta/project_ids_cell.dart';
import 'package:prodavan/features/meta/runtime/module_pick_context.dart';

typedef ProjectsRematerializeCallback = void Function(int scheduled, {required bool inline});
typedef WorkspaceOutdatedCallback = void Function();

/// Live cabinet / project-instance module data — mirrors SeedDataController API for interpreters.
class CabinetDataController extends ChangeNotifier with ModulePickContextMixin {
  CabinetDataController({
    required this.api,
    required this.cabinetId,
    required this.moduleId,
    required ModuleMetaManifest manifest,
    this.projectId,
  }) : _manifest = manifest;

  final ProdavanApi api;
  final String cabinetId;
  final String? projectId;
  final String moduleId;
  ModuleMetaManifest _manifest;
  final List<Map<String, dynamic>> _items = [];

  bool get _useProjectInstance => projectId != null && projectId!.isNotEmpty;

  ProjectsRematerializeCallback? onProjectsRematerialize;
  WorkspaceOutdatedCallback? onWorkspaceOutdated;

  ModuleMetaManifest get manifest => _manifest;

  List<Map<String, dynamic>> get items => List.unmodifiable(_items);

  Future<void> loadAll() async {
    _items.clear();
    for (final table in _manifest.tables) {
      final slug = table['slug'] as String?;
      if (slug == null || slug.isEmpty) continue;
      final rows = _useProjectInstance
          ? await api.listProjectRuntimeModuleDataRows(
              projectId: projectId!,
              moduleId: moduleId,
              tableSlug: slug,
            )
          : await api.listModuleDataRows(
              cabinetId: cabinetId,
              moduleId: moduleId,
              tableSlug: slug,
            );
      for (final row in rows) {
        _items.add({
          'table_slug': slug,
          'row_id': row['row_id'],
          'body': row['body'] is Map
              ? Map<String, dynamic>.from(row['body'] as Map)
              : <String, dynamic>{},
        });
      }
    }
    notifyListeners();
  }

  List<Map<String, dynamic>> itemsForTable(String tableSlug) {
    return _items.where((i) => i['table_slug'] == tableSlug).toList();
  }

  Map<String, dynamic>? itemById(String rowId) {
    for (final item in _items) {
      if (item['row_id'] == rowId) return item;
    }
    return null;
  }

  Map<String, dynamic> bodyFor(String rowId) {
    final item = itemById(rowId);
    final body = item?['body'];
    if (body is Map) return Map<String, dynamic>.from(body);
    return {};
  }

  void _emitRematerialize(Map<String, dynamic>? payload) {
    final remat = payload?['rematerialize'] ?? payload?['workspace_sync'];
    if (remat is Map) {
      final marked = remat['marked_outdated'];
      if (marked is int && marked > 0) {
        onWorkspaceOutdated?.call();
        return;
      }
      final scheduled = remat['scheduled'];
      if (scheduled is int && scheduled > 0) {
        final sync = remat['sync'];
        final inline = sync is List && sync.isNotEmpty;
        onProjectsRematerialize?.call(scheduled, inline: inline);
        return;
      }
      if (remat['mode'] == 'deferred') {
        onWorkspaceOutdated?.call();
        return;
      }
    }
    final outdated = payload?['workspace_outdated'];
    if (outdated is Map && (outdated['marked_outdated'] as int? ?? 0) > 0) {
      onWorkspaceOutdated?.call();
    }
  }

  Future<String> createRow(String tableSlug, {Map<String, dynamic>? initial}) async {
    final body = initial ?? defaultBodyForTable(tableSlug);
    final created = _useProjectInstance
        ? await api.createProjectRuntimeModuleDataRow(
            projectId: projectId!,
            moduleId: moduleId,
            tableSlug: tableSlug,
            body: body,
          )
        : await api.createModuleDataRow(
            cabinetId: cabinetId,
            moduleId: moduleId,
            tableSlug: tableSlug,
            body: body,
          );
    _emitRematerialize(created);
    final rowId = created['row_id'] as String;
    _items.add({
      'table_slug': tableSlug,
      'row_id': rowId,
      'body': Map<String, dynamic>.from(created['body'] as Map? ?? body),
    });
    notifyListeners();
    return rowId;
  }

  Future<void> upsertBody(String rowId, Map<String, dynamic> body) async {
    final item = itemById(rowId);
    if (item == null) return;
    final tableSlug = item['table_slug'] as String;
    final updated = _useProjectInstance
        ? await api.updateProjectRuntimeModuleDataRow(
            projectId: projectId!,
            moduleId: moduleId,
            tableSlug: tableSlug,
            rowId: rowId,
            body: body,
          )
        : await api.updateModuleDataRow(
            cabinetId: cabinetId,
            moduleId: moduleId,
            tableSlug: tableSlug,
            rowId: rowId,
            body: body,
          );
    _emitRematerialize(updated);
    for (var i = 0; i < _items.length; i++) {
      if (_items[i]['row_id'] == rowId) {
        _items[i] = {
          ..._items[i],
          'body': Map<String, dynamic>.from(updated['body'] as Map? ?? body),
        };
        break;
      }
    }
    notifyListeners();
  }

  Future<void> patchField(String rowId, String field, dynamic value) async {
    final body = bodyFor(rowId);
    body[field] = value;
    await upsertBody(rowId, body);
  }

  Future<void> deleteRow(String rowId) async {
    final item = itemById(rowId);
    if (item == null) {
      throw StateError('module data row not found: $rowId');
    }
    final deleted = _useProjectInstance
        ? await api.deleteProjectRuntimeModuleDataRow(
            projectId: projectId!,
            moduleId: moduleId,
            tableSlug: item['table_slug'] as String,
            rowId: rowId,
          )
        : await api.deleteModuleDataRow(
            cabinetId: cabinetId,
            moduleId: moduleId,
            tableSlug: item['table_slug'] as String,
            rowId: rowId,
          );
    _emitRematerialize(deleted);
    _items.removeWhere((i) => i['row_id'] == rowId);
    notifyListeners();
  }

  Future<void> invokeAction(String actionId, {String? rowId}) async {
    await api.invokeModuleAction(
      cabinetId: cabinetId,
      moduleId: moduleId,
      actionId: actionId,
      rowId: rowId,
    );
    await loadAll();
  }

  void refresh() => notifyListeners();

  Map<String, dynamic> defaultBodyForTable(String tableSlug) {
    final body = <String, dynamic>{};
    for (final c in _manifest.columnsForTable(tableSlug)) {
      final name = c['name'] as String? ?? '';
      if (name.isEmpty) continue;
      if (c.containsKey('default')) {
        body[name] = c['default'];
      }
    }
    return body;
  }

  List<AppEntityRow> entityRows(String tableSlug, Map<String, dynamic> uiJson) {
    final titleField = uiJson['title_field'] as String? ?? 'name';
    final subtitleFields = uiJson['subtitle_fields'] is List
        ? (uiJson['subtitle_fields'] as List).cast<String>()
        : <String>[];
    final columnDefs = uiJson['columns'] is List
        ? (uiJson['columns'] as List)
            .whereType<Map>()
            .map((c) => Map<String, dynamic>.from(c))
            .toList()
        : <Map<String, dynamic>>[];

    final dataRows = itemsForTable(tableSlug);
    return dataRows.map((item) {
      final rowId = item['row_id'] as String? ?? '';
      final body = item['body'] is Map
          ? Map<String, dynamic>.from(item['body'] as Map)
          : <String, dynamic>{};
      final title = body[titleField]?.toString() ?? rowId;
      final subtitle = subtitleFields
          .map((f) => body[f]?.toString())
          .whereType<String>()
          .where((s) => s.isNotEmpty)
          .join(' · ');
      final cells = <String, String>{};
      for (final col in columnDefs) {
        final field = col['field'] as String? ?? '';
        if (field.isEmpty) continue;
        cells[field] = _cellValue(item, body, col);
      }
      return AppEntityRow(
        id: rowId,
        title: title.isEmpty ? rowId : title,
        subtitle: subtitle.isEmpty ? null : subtitle,
        cells: cells,
      );
    }).toList();
  }

  String _cellValue(
    Map<String, dynamic> item,
    Map<String, dynamic> body,
    Map<String, dynamic> col,
  ) {
    final source = col['source']?.toString();
    if (source == 'row.created_at' || source == 'row.updated_at') {
      final key = source == 'row.created_at' ? 'created_at' : 'updated_at';
      return _formatEnvelopeDate(item[key]);
    }
    final field = col['field'] as String? ?? '';
    final raw = body[field];
    if (field == 'project_ids') {
      return formatProjectIdsCell(raw);
    }
    return raw?.toString() ?? '';
  }

  String _formatEnvelopeDate(dynamic raw) {
    if (raw == null) return '';
    final text = raw.toString().trim();
    if (text.isEmpty) return '';
    final parsed = DateTime.tryParse(text);
    if (parsed == null) return text;
    final local = parsed.toLocal();
    final y = local.year.toString().padLeft(4, '0');
    final m = local.month.toString().padLeft(2, '0');
    final d = local.day.toString().padLeft(2, '0');
    return '$y-$m-$d';
  }
}
