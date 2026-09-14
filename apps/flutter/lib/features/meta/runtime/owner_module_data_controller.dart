import 'package:flutter/foundation.dart';

import 'package:prodavan/core/api/admin_api.dart';
import 'package:prodavan/core/api/company_api.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/features/meta/module_cell_format.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/features/meta/runtime/module_pick_context.dart';

/// Live platform / company module-instance data for shell nav modules.
///
/// Mirrors [CabinetDataController] so meta interpreters stay owner-agnostic.
/// Deletes only the caller's instance copy (no cascade to delegated children).
class OwnerModuleDataController extends ChangeNotifier with ModulePickContextMixin {
  OwnerModuleDataController.platform({
    required AdminApi api,
    required this.moduleId,
    required ModuleMetaManifest manifest,
  })  : _adminApi = api,
        _companyApi = null,
        companyId = null,
        _manifest = manifest;

  OwnerModuleDataController.company({
    required CompanyApi api,
    required this.companyId,
    required this.moduleId,
    required ModuleMetaManifest manifest,
  })  : _adminApi = null,
        _companyApi = api,
        _manifest = manifest;

  final AdminApi? _adminApi;
  final CompanyApi? _companyApi;
  final String? companyId;
  final String moduleId;
  ModuleMetaManifest _manifest;
  final List<Map<String, dynamic>> _items = [];

  bool get _isCompany => companyId != null && companyId!.isNotEmpty;

  ModuleMetaManifest get manifest => _manifest;

  List<Map<String, dynamic>> get items => List.unmodifiable(_items);

  Future<void> loadAll() async {
    _items.clear();
    for (final table in _manifest.tables) {
      final slug = table['slug'] as String?;
      if (slug == null || slug.isEmpty) continue;
      final rows = _isCompany
          ? await _companyApi!.listModuleDataRows(
              companyId: companyId!,
              moduleId: moduleId,
              tableSlug: slug,
            )
          : await _adminApi!.listModuleDataRows(
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
          if (row['created_at'] != null) 'created_at': row['created_at'],
          if (row['updated_at'] != null) 'updated_at': row['updated_at'],
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

  Future<String> createRow(String tableSlug, {Map<String, dynamic>? initial}) async {
    final body = initial ?? defaultBodyForTable(tableSlug);
    final created = _isCompany
        ? await _companyApi!.createModuleDataRow(
            companyId: companyId!,
            moduleId: moduleId,
            tableSlug: tableSlug,
            body: body,
          )
        : await _adminApi!.createModuleDataRow(
            moduleId: moduleId,
            tableSlug: tableSlug,
            body: body,
          );
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
    if (item == null) {
      throw StateError('module data row not found: $rowId');
    }
    final tableSlug = item['table_slug'] as String;
    final updated = _isCompany
        ? await _companyApi!.updateModuleDataRow(
            companyId: companyId!,
            moduleId: moduleId,
            tableSlug: tableSlug,
            rowId: rowId,
            body: body,
          )
        : await _adminApi!.updateModuleDataRow(
            moduleId: moduleId,
            tableSlug: tableSlug,
            rowId: rowId,
            body: body,
          );
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
    final tableSlug = item['table_slug'] as String;
    if (_isCompany) {
      await _companyApi!.deleteModuleDataRow(
        companyId: companyId!,
        moduleId: moduleId,
        tableSlug: tableSlug,
        rowId: rowId,
      );
    } else {
      await _adminApi!.deleteModuleDataRow(
        moduleId: moduleId,
        tableSlug: tableSlug,
        rowId: rowId,
      );
    }
    _items.removeWhere((i) => i['row_id'] == rowId);
    notifyListeners();
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
        final source = col['source']?.toString();
        final format = col['format']?.toString();
        if (source != null ||
            format != null ||
            field == 'project_ids' ||
            field == 'path' ||
            field == 'files_json' ||
            field == 'prompts_count') {
          cells[field] = formatModuleCell(
            item: item,
            body: body,
            col: col,
            tableSlug: tableSlug,
            itemsForTable: itemsForTable,
          );
        } else {
          cells[field] = _formatCell(body[field], tableSlug, field);
        }
      }
      return AppEntityRow(
        id: rowId,
        title: title.isEmpty ? rowId : title,
        subtitle: subtitle.isEmpty ? null : subtitle,
        cells: cells,
      );
    }).toList();
  }

  String _formatCell(dynamic value, String tableSlug, String field) {
    if (value == null) return '';
    if (value is bool) return value ? 'true' : 'false';
    if (value is Map && value.containsKey('filename')) {
      return value['filename']?.toString() ?? 'file';
    }
    if (value is Map || value is List) return '…';
    final col = _manifest.columnsForTable(tableSlug).cast<Map<String, dynamic>?>().firstWhere(
          (c) => c?['name'] == field,
          orElse: () => null,
        );
    if (col != null && col['type'] == 'enum') {
      final en = col['enum'];
      if (en is Map && en['labels'] is Map) {
        final labels = Map<String, dynamic>.from(en['labels'] as Map);
        final key = value.toString();
        if (labels.containsKey(key)) return labels[key].toString();
      }
    }
    if (col != null && col['type'] == 'ref') {
      return formatModuleCell(
        item: const {},
        body: {field: value},
        col: {'field': field, 'type': 'ref', 'ref': col['ref']},
        tableSlug: tableSlug,
        itemsForTable: itemsForTable,
      );
    }
    return value.toString();
  }
}
