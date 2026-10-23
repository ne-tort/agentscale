import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';

/// Позиции тендера (tenders.lines_json) — редактируемая подтаблица прямо в
/// карточке тендера (виджет `tender_lines_table`). Элемент: {id, title,
/// part_number, qty}; id не задан — генерируется при добавлении. Пишет весь
/// массив lines_json через onChanged (персист — _persist формы).
class TenderLinesTable extends StatefulWidget {
  const TenderLinesTable({
    super.key,
    required this.value,
    required this.readOnly,
    required this.onChanged,
  });

  final dynamic value;
  final bool readOnly;
  final void Function(List<Map<String, dynamic>> lines) onChanged;

  @override
  State<TenderLinesTable> createState() => _TenderLinesTableState();
}

class _TenderLinesTableState extends State<TenderLinesTable> {
  List<Map<String, dynamic>> _lines() {
    final raw = widget.value;
    if (raw is! List) return const [];
    return [
      for (final item in raw)
        if (item is Map) Map<String, dynamic>.from(item),
    ];
  }

  void _emit(List<Map<String, dynamic>> next) => widget.onChanged(next);

  void _addLine() {
    final lines = [..._lines()];
    var maxN = 0;
    for (final l in lines) {
      final id = l['id']?.toString() ?? '';
      final m = RegExp(r'^tl(\d+)$').firstMatch(id);
      if (m != null) {
        final n = int.tryParse(m.group(1)!) ?? 0;
        if (n > maxN) maxN = n;
      }
    }
    lines.add({
      'id': 'tl${maxN + 1}',
      'title': '',
      'part_number': '',
      'qty': 1,
    });
    _emit(lines);
  }

  void _removeLine(int index) {
    final lines = [..._lines()];
    lines.removeAt(index);
    _emit(lines);
  }

  void _patchLine(int index, String key, dynamic value) {
    final lines = [..._lines()];
    lines[index] = {...lines[index], key: value};
    _emit(lines);
  }

  @override
  Widget build(BuildContext context) {
    final colors = context.appColors;
    final lines = _lines();
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: AppSpacing.xs),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Padding(
            padding: const EdgeInsets.symmetric(
              horizontal: AppSpacing.md,
              vertical: AppSpacing.xs,
            ),
            child: Row(
              children: [
                Expanded(
                  flex: 4,
                  child: Text(
                    'Позиции тендера',
                    style: Theme.of(context).textTheme.titleSmall,
                  ),
                ),
                if (!widget.readOnly)
                  IconButton(
                    tooltip: 'Добавить позицию',
                    icon: const Icon(Icons.add, size: 20),
                    onPressed: _addLine,
                  ),
              ],
            ),
          ),
          if (lines.isEmpty)
            Padding(
              padding: const EdgeInsets.all(AppSpacing.md),
              child: Text(
                'Позиций пока нет — добавьте вручную или через агента',
                style: Theme.of(context)
                    .textTheme
                    .bodyMedium
                    ?.copyWith(color: colors.muted),
              ),
            )
          else
            for (var i = 0; i < lines.length; i++) _row(context, i, lines[i]),
        ],
      ),
    );
  }

  Widget _row(BuildContext context, int index, Map<String, dynamic> line) {
    final colors = context.appColors;
    return Padding(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.md,
        vertical: 2,
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.center,
        children: [
          Expanded(
            flex: 4,
            child: _LineCell(
              value: line['title']?.toString() ?? '',
              hint: 'Название',
              readOnly: widget.readOnly,
              onChanged: (v) => _patchLine(index, 'title', v),
            ),
          ),
          const SizedBox(width: AppSpacing.sm),
          Expanded(
            flex: 2,
            child: _LineCell(
              value: line['part_number']?.toString() ?? '',
              hint: 'Партномер',
              readOnly: widget.readOnly,
              onChanged: (v) => _patchLine(index, 'part_number', v),
            ),
          ),
          const SizedBox(width: AppSpacing.sm),
          SizedBox(
            width: 72,
            child: _LineCell(
              value: '${line['qty'] ?? 1}',
              hint: 'Кол-во',
              numeric: true,
              readOnly: widget.readOnly,
              onChanged: (v) {
                final n = num.tryParse(v.trim());
                if (n != null && n > 0) _patchLine(index, 'qty', n);
              },
            ),
          ),
          if (!widget.readOnly)
            IconButton(
              tooltip: 'Удалить позицию',
              icon: Icon(Icons.close, size: 18, color: colors.muted),
              onPressed: () => _removeLine(index),
            ),
        ],
      ),
    );
  }
}

class _LineCell extends StatefulWidget {
  const _LineCell({
    required this.value,
    required this.hint,
    required this.readOnly,
    required this.onChanged,
    this.numeric = false,
  });

  final String value;
  final String hint;
  final bool readOnly;
  final bool numeric;
  final void Function(String value) onChanged;

  @override
  State<_LineCell> createState() => _LineCellState();
}

class _LineCellState extends State<_LineCell> {
  late final TextEditingController _ctrl;

  @override
  void initState() {
    super.initState();
    _ctrl = TextEditingController(text: widget.value);
  }

  @override
  void didUpdateWidget(_LineCell old) {
    super.didUpdateWidget(old);
    if (old.value != widget.value && _ctrl.text != widget.value) {
      _ctrl.text = widget.value;
    }
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (widget.readOnly) {
      return Text(
        widget.value.isEmpty ? '—' : widget.value,
        style: Theme.of(context).textTheme.bodyMedium,
      );
    }
    return TextField(
      controller: _ctrl,
      keyboardType: widget.numeric ? TextInputType.number : TextInputType.text,
      textAlign: widget.numeric ? TextAlign.center : TextAlign.start,
      decoration: InputDecoration(
        isDense: true,
        hintText: widget.hint,
        contentPadding:
            const EdgeInsets.symmetric(vertical: 6, horizontal: 6),
        border: const OutlineInputBorder(),
      ),
      onSubmitted: widget.onChanged,
      onEditingComplete: () => widget.onChanged(_ctrl.text),
    );
  }
}
