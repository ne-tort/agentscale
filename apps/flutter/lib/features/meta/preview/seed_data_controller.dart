import 'package:flutter/foundation.dart';

import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/features/meta/project_ids_cell.dart';
import 'package:prodavan/features/meta/runtime/module_pick_context.dart';

/// Mutable seed_rows editor backing module preview CRUD.
class SeedDataController extends ChangeNotifier with ModulePickContextMixin {
  SeedDataController(ModuleMetaManifest manifest)
      : _manifest = manifest,
        _items = [
          for (final item in manifest.seedRows) Map<String, dynamic>.from(item),
        ];

  ModuleMetaManifest _manifest;
  final List<Map<String, dynamic>> _items;

  ModuleMetaManifest get manifest => _manifest;

  ModuleMetaManifest get manifestWithSeed => _manifest.copyWith(
        seedRows: [
          for (final item in _items) Map<String, dynamic>.from(item),
        ],
      );

  List<Map<String, dynamic>> get items => List.unmodifiable(_items);

  void replaceManifest(ModuleMetaManifest manifest) {
    _manifest = manifest;
    _items
      ..clear()
      ..addAll([
        for (final item in manifest.seedRows) Map<String, dynamic>.from(item),
      ]);
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

  /// Create a seed row with column defaults applied. Returns [row_id].
  String createRow(String tableSlug) {
    final rowId = 'seed_${DateTime.now().microsecondsSinceEpoch.toRadixString(16)}';
    final body = defaultBodyForTable(tableSlug);
    _items.add({
      'table_slug': tableSlug,
      'row_id': rowId,
      'body': body,
    });
    notifyListeners();
    return rowId;
  }

  void upsertBody(String rowId, Map<String, dynamic> body) {
    for (var i = 0; i < _items.length; i++) {
      if (_items[i]['row_id'] == rowId) {
        _items[i] = {
          ..._items[i],
          'body': Map<String, dynamic>.from(body),
        };
        notifyListeners();
        return;
      }
    }
  }

  void patchField(String rowId, String field, dynamic value) {
    final body = bodyFor(rowId);
    body[field] = value;
    upsertBody(rowId, body);
  }

  void deleteRow(String rowId) {
    _items.removeWhere((i) => i['row_id'] == rowId);
    notifyListeners();
  }

  /// Re-emit for UI refresh (toolbar).
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
        if (source == 'row.created_at' || source == 'row.updated_at') {
          final key = source == 'row.created_at' ? 'created_at' : 'updated_at';
          cells[field] = _formatEnvelopeDate(item[key]);
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

  String _formatCell(dynamic value, String tableSlug, String field) {
    if (field == 'project_ids') {
      return formatProjectIdsCell(value);
    }
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
    return value.toString();
  }
}
