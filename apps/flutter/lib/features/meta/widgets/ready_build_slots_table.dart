import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';

/// Таблица слотов готовой сборки прямо на странице сборки (не отдельная
/// подстраница). Строка: тип, требования, количество (редактируемое для
/// ОЗУ/накопителей), разрешённый компонент (клик → выбор альтернативы, если
/// есть), цена, сумма. Пишет строки `ready_build_slots`.
class ReadyBuildSlotsTable extends StatelessWidget {
  const ReadyBuildSlotsTable({
    super.key,
    required this.seeds,
    required this.buildId,
    required this.slotsTable,
    required this.readOnly,
    this.onOpenPick,
    this.pickView = 'part_cards_pick',
  });

  final dynamic seeds;
  final String? buildId;
  final String slotsTable;
  final bool readOnly;
  final void Function(String viewSlug, {String? rowId})? onOpenPick;
  final String pickView;

  static const _qtyEditableTypes = {'etype_ram', 'etype_storage'};

  List<Map<String, dynamic>> _slots() {
    if (buildId == null || buildId!.isEmpty) return const [];
    final out = <Map<String, dynamic>>[];
    for (final item in seeds.itemsForTable(slotsTable) as List) {
      if (item is! Map) continue;
      final body = item['body'];
      if (body is! Map) continue;
      if (body['build_id']?.toString() != buildId) continue;
      out.add({'row_id': item['row_id'], ...Map<String, dynamic>.from(body)});
    }
    return out;
  }

  @override
  Widget build(BuildContext context) {
    final colors = context.appColors;
    final slots = _slots();
    if (slots.isEmpty) {
      return Padding(
        padding: const EdgeInsets.all(AppSpacing.md),
        child: Text(
          'Слотов пока нет — задайте их в «Состав»',
          style: Theme.of(context)
              .textTheme
              .bodyMedium
              ?.copyWith(color: colors.muted),
        ),
      );
    }
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: AppSpacing.xs),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          for (final slot in slots) _row(context, slot, colors),
        ],
      ),
    );
  }

  Widget _row(BuildContext context, Map<String, dynamic> slot, dynamic colors) {
    final slotId = slot['row_id']?.toString() ?? '';
    final typeId = slot['type_id']?.toString() ?? '';
    final typeName = slot['type_name']?.toString() ?? typeId;
    final constraints = slot['constraints_label']?.toString() ?? '';
    final qty = (slot['qty'] as num?) ?? 1;
    final resolvedTitle = slot['resolved_title']?.toString() ?? '';
    final resolvedPrice = (slot['resolved_price'] as num?)?.toDouble();
    final lineTotal = (slot['line_total'] as num?)?.toDouble();
    final alternatives = (slot['alternatives_count'] as num?)?.toInt() ?? 0;
    final qtyEditable = !readOnly && _qtyEditableTypes.contains(typeId);
    final replaceable = !readOnly && alternatives > 0 && onOpenPick != null;

    return Padding(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.md,
        vertical: AppSpacing.xs,
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.center,
        children: [
          Expanded(
            flex: 3,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(typeName, style: Theme.of(context).textTheme.bodyMedium),
                if (constraints.isNotEmpty)
                  Text(
                    constraints,
                    style: Theme.of(context)
                        .textTheme
                        .bodySmall
                        ?.copyWith(color: colors.muted),
                  ),
                InkWell(
                  // замена компонента: кликабельно только при альтернативах
                  onTap: replaceable
                      ? () => onOpenPick!(_pickViewFor(slot), rowId: slotId)
                      : null,
                  child: Text(
                    resolvedTitle.isEmpty ? '— не подобран —' : resolvedTitle,
                    style: Theme.of(context).textTheme.bodySmall?.copyWith(
                          color: replaceable
                              ? Theme.of(context).colorScheme.primary
                              : (resolvedTitle.isEmpty
                                  ? colors.muted
                                  : null),
                        ),
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(width: AppSpacing.sm),
          if (qtyEditable)
            _QtyCell(qty: qty, onChanged: (v) => _saveQty(slotId, v))
          else
            Text(
              '×${qty.toInt()}',
              style: Theme.of(context).textTheme.bodySmall,
            ),
          const SizedBox(width: AppSpacing.sm),
          SizedBox(
            width: 110,
            child: Text(
              resolvedPrice == null ? '—' : '${_fmt(resolvedPrice)} ₽',
              textAlign: TextAlign.end,
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ),
          SizedBox(
            width: 110,
            child: Text(
              lineTotal == null ? '—' : '${_fmt(lineTotal)} ₽',
              textAlign: TextAlign.end,
              style: Theme.of(context)
                  .textTheme
                  .bodyMedium
                  ?.copyWith(fontWeight: FontWeight.w600),
            ),
          ),
        ],
      ),
    );
  }

  // pick-вид задан в конфиге виджета на странице сборки
  String _pickViewFor(Map<String, dynamic> slot) => pickView;

  String _fmt(double v) {
    if (v == v.roundToDouble()) return v.toInt().toString();
    // 7402.80 → 7402.8 (срезаем хвостовые нули, не значащие цифры)
    return v
        .toStringAsFixed(2)
        .replaceAll(RegExp(r'0+$'), '')
        .replaceAll(RegExp(r'\.$'), '');
  }

  void _saveQty(String slotId, int qty) {
    if (qty < 1) return;
    final patch = seeds.patchField(slotId, 'qty', qty);
    if (patch is Future) {
      patch.catchError((_) {});
    }
  }
}

class _QtyCell extends StatefulWidget {
  const _QtyCell({required this.qty, required this.onChanged});

  final num qty;
  final void Function(int qty) onChanged;

  @override
  State<_QtyCell> createState() => _QtyCellState();
}

class _QtyCellState extends State<_QtyCell> {
  late final TextEditingController _ctrl;

  @override
  void initState() {
    super.initState();
    _ctrl = TextEditingController(text: widget.qty.toInt().toString());
  }

  @override
  void didUpdateWidget(_QtyCell old) {
    super.didUpdateWidget(old);
    final next = widget.qty.toInt().toString();
    if (_ctrl.text != next) _ctrl.text = next;
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: 56,
      child: TextField(
        controller: _ctrl,
        keyboardType: TextInputType.number,
        textAlign: TextAlign.center,
        decoration: const InputDecoration(
          isDense: true,
          contentPadding: EdgeInsets.symmetric(vertical: 6, horizontal: 4),
          border: OutlineInputBorder(),
        ),
        onSubmitted: (raw) {
          final v = int.tryParse(raw.trim());
          if (v != null) widget.onChanged(v);
        },
        onEditingComplete: () {
          final v = int.tryParse(_ctrl.text.trim());
          if (v != null) widget.onChanged(v);
        },
      ),
    );
  }
}
