/// Shared cell formatters for module collection columns.
library;

import 'package:prodavan/features/meta/project_ids_cell.dart';
import 'package:prodavan/features/meta/workspace_path.dart';

String formatListCountCell(dynamic value) {
  if (value is! List) return '0';
  return '${value.length}';
}

/// Sum of `files_json` lengths on [relatedRows] whose [foreignKey] matches [rowId].
int aggregateRelatedListCount({
  required List<Map<String, dynamic>> relatedRows,
  required String foreignKey,
  required String rowId,
  required String listField,
}) {
  var total = 0;
  for (final item in relatedRows) {
    final body = item['body'];
    if (body is! Map) continue;
    if (body[foreignKey]?.toString() != rowId) continue;
    final list = body[listField];
    if (list is List) total += list.length;
  }
  return total;
}

String formatModuleCell({
  required Map<String, dynamic> item,
  required Map<String, dynamic> body,
  required Map<String, dynamic> col,
  required String tableSlug,
  List<Map<String, dynamic>> Function(String tableSlug)? itemsForTable,
}) {
  final source = col['source']?.toString();
  if (source == 'row.created_at' || source == 'row.updated_at') {
    final key = source == 'row.created_at' ? 'created_at' : 'updated_at';
    return _formatEnvelopeDate(item[key]);
  }
  if (source == 'aggregate.prompt_paths.files_json') {
    final rows = itemsForTable?.call('prompt_paths') ?? const [];
    final rowId = item['row_id']?.toString() ?? '';
    return '${aggregateRelatedListCount(
      relatedRows: rows,
      foreignKey: 'profile_id',
      rowId: rowId,
      listField: 'files_json',
    )}';
  }

  final field = col['field'] as String? ?? '';
  final format = col['format']?.toString();
  final raw = body[field];

  if (format == 'list_count' || (field == 'files_json' && format == 'count')) {
    return formatListCountCell(raw);
  }
  if (field == 'project_ids') {
    return formatProjectIdsCell(raw);
  }
  if (field == 'path') {
    return formatPathCell(raw);
  }
  if (format == 'index_progress') {
    // «В процессе (x из y)»: dynamic progress from the worker heartbeat
    // fields; other statuses fall through to the enum labels below.
    if (raw?.toString() == 'indexing') {
      final indexed = _asInt(body['indexed_count']);
      if (indexed != null) {
        final total = _asInt(body['total_rows']);
        final totalText = total != null ? ' из $total' : '';
        return 'В процессе ($indexed$totalText)';
      }
      return 'В процессе';
    }
  }

  final enumMeta = col['enum'];
  if (enumMeta is Map && enumMeta['labels'] is Map) {
    final labels = Map<String, dynamic>.from(enumMeta['labels'] as Map);
    final key = raw?.toString() ?? '';
    if (labels.containsKey(key)) return labels[key].toString();
  }

  if (col['type']?.toString() == 'ref' && raw != null && itemsForTable != null) {
    final ref = col['ref'];
    final tableSlug = ref is Map ? ref['table_slug']?.toString() : null;
    if (tableSlug != null && tableSlug.isNotEmpty) {
      final id = raw.toString();
      for (final related in itemsForTable(tableSlug)) {
        if (related['row_id']?.toString() != id) continue;
        final relatedBody = related['body'];
        if (relatedBody is Map) {
          final title = relatedBody['title'] ?? relatedBody['name'];
          if (title != null && title.toString().trim().isNotEmpty) {
            return title.toString();
          }
        }
        break;
      }
    }
  }

  return raw?.toString() ?? '';
}

int? _asInt(dynamic raw) {
  if (raw is num) return raw.toInt();
  if (raw is String) return int.tryParse(raw.trim());
  return null;
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
