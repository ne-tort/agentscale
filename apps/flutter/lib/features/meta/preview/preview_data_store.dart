import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';

abstract final class PreviewDataStore {
  static List<Map<String, dynamic>> rowsForTable(
    ModuleMetaManifest manifest,
    String tableSlug, {
    int count = 3,
  }) {
    final cols = manifest.columnsForTable(tableSlug);
    return List.generate(count, (i) {
      final body = <String, dynamic>{};
      for (final c in cols) {
        final name = c['name'] as String? ?? '';
        if (name.isEmpty) continue;
        body[name] = _sampleValue(c, i);
      }
      return body;
    });
  }

  static List<AppEntityRow> entityRows(
    ModuleMetaManifest manifest,
    String tableSlug,
    Map<String, dynamic> uiJson,
  ) {
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

    final dataRows = rowsForTable(manifest, tableSlug);
    return dataRows.asMap().entries.map((entry) {
      final i = entry.key;
      final body = entry.value;
      final title = body[titleField]?.toString() ?? 'Row ${i + 1}';
      final subtitle = subtitleFields
          .map((f) => body[f]?.toString())
          .whereType<String>()
          .where((s) => s.isNotEmpty)
          .join(' · ');
      final cells = <String, String>{};
      for (final f in columnFields) {
        cells[f] = _formatCell(body[f], manifest, tableSlug, f);
      }
      return AppEntityRow(
        id: 'preview_row_$i',
        title: title,
        subtitle: subtitle.isEmpty ? null : subtitle,
        cells: cells,
      );
    }).toList();
  }

  static dynamic _sampleValue(Map<String, dynamic> column, int index) {
    final type = column['type'] as String? ?? 'text';
    switch (type) {
      case 'number':
        return index + 1;
      case 'bool':
        return index.isEven;
      case 'datetime':
        return '2026-08-27T12:00:00Z';
      case 'json':
        return {'k': index};
      case 'enum':
        final en = column['enum'];
        if (en is Map && en['values'] is List) {
          final values = (en['values'] as List).cast<String>();
          if (values.isNotEmpty) return values[index % values.length];
        }
        return 'active';
      case 'ref':
        return 'preview_ref_$index';
      case 'file_ref':
        return {'filename': 'file_$index.pdf', 'object_key': 'preview/$index'};
      default:
        return 'Sample ${index + 1}';
    }
  }

  static String _formatCell(
    dynamic value,
    ModuleMetaManifest manifest,
    String tableSlug,
    String field,
  ) {
    if (value == null) return '';
    if (value is bool) return value ? 'true' : 'false';
    if (value is Map && value.containsKey('filename')) {
      return value['filename']?.toString() ?? 'file';
    }
    if (value is Map || value is List) return '…';
    final col = manifest.columnsForTable(tableSlug).cast<Map<String, dynamic>?>().firstWhere(
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
