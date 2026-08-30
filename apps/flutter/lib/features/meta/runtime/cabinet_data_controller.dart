import 'package:flutter/foundation.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';

typedef ProjectsRematerializeCallback = void Function(int scheduled, {required bool inline});

/// Live cabinet module data — mirrors SeedDataController API for interpreters.
class CabinetDataController extends ChangeNotifier {
  CabinetDataController({
    required this.api,
    required this.cabinetId,
    required this.moduleId,
    required ModuleMetaManifest manifest,
  }) : _manifest = manifest;

  final ProdavanApi api;
  final String cabinetId;
  final String moduleId;
  ModuleMetaManifest _manifest;
  final List<Map<String, dynamic>> _items = [];

  ProjectsRematerializeCallback? onProjectsRematerialize;

  ModuleMetaManifest get manifest => _manifest;

  List<Map<String, dynamic>> get items => List.unmodifiable(_items);

  Future<void> loadAll() async {
    _items.clear();
    for (final table in _manifest.tables) {
      final slug = table['slug'] as String?;
      if (slug == null || slug.isEmpty) continue;
      final rows = await api.listModuleDataRows(
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
    final remat = payload?['rematerialize'];
    if (remat is! Map) return;
    final scheduled = remat['scheduled'];
    if (scheduled is! int || scheduled <= 0) return;
    final sync = remat['sync'];
    final inline = sync is List && sync.isNotEmpty;
    onProjectsRematerialize?.call(scheduled, inline: inline);
  }

  Future<String> createRow(String tableSlug, {Map<String, dynamic>? initial}) async {
    final body = initial ?? defaultBodyForTable(tableSlug);
    final created = await api.createModuleDataRow(
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
    final updated = await api.updateModuleDataRow(
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
    if (item == null) return;
    final deleted = await api.deleteModuleDataRow(
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
    final columnFields = uiJson['columns'] is List
        ? (uiJson['columns'] as List)
            .map((c) => (c as Map)['field'] as String? ?? '')
            .where((f) => f.isNotEmpty)
            .toList()
        : <String>[];

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
      for (final f in columnFields) {
        cells[f] = body[f]?.toString() ?? '';
      }
      return AppEntityRow(
        id: rowId,
        title: title.isEmpty ? rowId : title,
        subtitle: subtitle.isEmpty ? null : subtitle,
        cells: cells,
      );
    }).toList();
  }
}
