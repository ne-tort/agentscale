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

  if (format == 'file_name') {
    // FileRef map (module file columns): show the uploaded filename.
    if (raw is Map) {
      final name = raw['filename']?.toString() ?? '';
      if (name.isNotEmpty) return name;
      final size = raw['size'];
      if (size is num) return '$size B';
      return 'файл';
    }
    return '';
  }
  if (format == 'bool_yes_no') {
    return raw == true ? 'Да' : 'Нет';
  }
  if (format == 'margin_pair') {
    // «Закупка»: маржа как «{₽} ({pct}%)» — sum_margin_rub + margin_pct.
    final rub = _asDouble(body['sum_margin_rub']) ?? 0;
    final pct = _asDouble(body['margin_pct']) ?? 0;
    return '${rub.toStringAsFixed(2)} (${pct.toStringAsFixed(1)}%)';
  }
  if (format == 'benefit_static') {
    // Серверный бейдж «Выгода» (пайплайн считает per-offer): текст из
    // benefit_label; тон красит виджет в collection_interpreter.
    return body['benefit_label']?.toString() ?? '';
  }
  if (format == 'budget_calc') {
    // Equipment budgeting: computed cell, value lives in col['variant'].
    return formatBudgetCalcCell(body: body, col: col);
  }
  if (format == 'budget_price_in') {
    // «Вход с НДС»: честные надписи вместо нуля/пустоты; warning-окраску
    // добавляет collection_interpreter (cellWidgets).
    return budgetPriceInLabel(body) ?? raw?.toString() ?? '';
  }
  if (format == 'offer_price') {
    // Цена оффера/лица группы: текстовые «цены» («Уточняйте») не парсятся
    // в число на индексации → null → честная надпись + warning-окраска.
    if (raw == null) return 'Нет цены';
    return raw.toString();
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

/// Equipment budgeting computed cell (`format: budget_calc` on a column).
///
/// Row body inputs (budget_lines): `price_in` (purchase price, with VAT),
/// `qty` (default 1), `vat` (default 0.22), `markup` (default 0.1).
/// Column `variant` (Excel template canon, шаблон.xlsx):
/// - `price_out` — price with margin, with VAT: `price_in * (1 + markup)`
///   (template column K);
/// - `price_no_vat` — price with margin, without VAT: `price_out / (1 + vat)`
///   (template column J);
/// - `margin_total` — margin for the total as `{n} ({y}%)`:
///   `qty * (price_out - price_in)` ₽ + markup % (template column O).
///
/// Empty string when `price_in` is missing (nothing to compute from).
String formatBudgetCalcCell({
  required Map<String, dynamic> body,
  required Map<String, dynamic> col,
}) {
  final priceIn = _asDouble(body['price_in']);
  if (priceIn == null) return '';
  final qty = _asDouble(body['qty']) ?? 1.0;
  final markup = _asDouble(body['markup']) ?? 0.1;
  final vat = _asDouble(body['vat']);
  final priceOut = priceIn * (1 + markup);
  final variant = col['variant']?.toString();
  final double value;
  switch (variant) {
    case 'margin_total':
      // «Маржа»: {сумма ₽} ({наценка %}) — наценка строки и есть маржа-%.
      final marginRub = qty * (priceOut - priceIn);
      return '${marginRub.toStringAsFixed(2)} (${(markup * 100).toStringAsFixed(1)}%)';
    case 'price_no_vat':
      if (vat == null || vat < 0) return '';
      value = priceOut / (1 + vat);
    default:
      value = priceOut;
  }
  return value.toStringAsFixed(2);
}

/// «Вход с НДС» (budget_lines): подпись вместо числа, когда цены нет или
/// оффер под заказ. null — обычная числовая ячейка.
/// - цены нет → «Нет цены»;
/// - цены нет и под заказ → «Под заказ»;
/// - цена есть и под заказ → «{цена} (Под заказ)».
String? budgetPriceInLabel(Map<String, dynamic> body) {
  final priceIn = _asDouble(body['price_in']);
  final onOrder = body['on_order'] == true;
  if (priceIn == null) return onOrder ? 'Под заказ' : 'Нет цены';
  if (onOrder) return '${priceIn.toStringAsFixed(2)} (Под заказ)';
  return null;
}

int? _asInt(dynamic raw) {
  if (raw is num) return raw.toInt();
  if (raw is String) return int.tryParse(raw.trim());
  return null;
}

double? _asDouble(dynamic raw) {
  if (raw is num) return raw.toDouble();
  if (raw is String) return double.tryParse(raw.trim());
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
