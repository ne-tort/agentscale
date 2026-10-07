import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/features/meta/runtime/cabinet_data_controller.dart';
import 'package:prodavan/features/meta/runtime/runtime_data_adapter.dart';

/// Лист сопоставления найденного товара (OS src_hash) с позицией заказчика.
///
/// Используется с «Поиска товаров» и «Мастер прайса»: тап по товару →
/// список позиций заказчика; звезда (или тап по позиции) закрепляет товар
/// за позицией (дополнительно ставит выбранным оффером — «звезда» в UI),
/// после успеха текст позиции становится зелёным.
class EquipmentMatchSheet extends StatefulWidget {
  const EquipmentMatchSheet({
    super.key,
    required this.adapter,
    required this.actionId,
    required this.srcHash,
    this.productTitle = '',
  });

  final RuntimeDataAdapter adapter;
  final String actionId;
  final String srcHash;
  final String productTitle;

  static Future<void> show({
    required BuildContext context,
    required RuntimeDataAdapter adapter,
    required String actionId,
    required String srcHash,
    String? productTitle,
  }) {
    return showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (_) => EquipmentMatchSheet(
        adapter: adapter,
        actionId: actionId,
        srcHash: srcHash,
        productTitle: productTitle ?? '',
      ),
    );
  }

  @override
  State<EquipmentMatchSheet> createState() => _EquipmentMatchSheetState();
}

class _EquipmentMatchSheetState extends State<EquipmentMatchSheet> {
  String? _busyLineId;

  List<Map<String, dynamic>> get _lines =>
      widget.adapter.itemsForTable('request_lines');

  List<Map<String, dynamic>> get _offers =>
      widget.adapter.itemsForTable('found_offers');

  bool _matched(String lineId) {
    for (final offer in _offers) {
      final body = offer['body'];
      if (body is! Map) continue;
      if (body['line_id']?.toString() != lineId) continue;
      if (body['is_selected'] != true) continue;
      if (body['src_hash']?.toString() == widget.srcHash) return true;
    }
    return false;
  }

  Future<void> _match(String lineId) async {
    if (_busyLineId != null) return;
    setState(() => _busyLineId = lineId);
    try {
      await widget.adapter.controller.invokeAction(
        widget.actionId,
        rowId: lineId,
        params: {'src_hash': widget.srcHash},
      );
      if (mounted) setState(() {});
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    } finally {
      if (mounted) setState(() => _busyLineId = null);
    }
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final colors = context.appColors;
    final lines = _lines;
    return SafeArea(
      child: ConstrainedBox(
        constraints: BoxConstraints(
          maxHeight: MediaQuery.sizeOf(context).height * 0.7,
        ),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(
                'Сопоставить с позицией заказчика',
                style: Theme.of(context).textTheme.titleMedium,
              ),
              if (widget.productTitle.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 4),
                  child: Text(
                    widget.productTitle,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(color: scheme.onSurfaceVariant, fontSize: 12),
                  ),
                ),
              const SizedBox(height: 8),
              if (lines.isEmpty)
                Padding(
                  padding: const EdgeInsets.all(12),
                  child: Text(
                    'В текущем чате нет позиций заказчика',
                    style: TextStyle(color: scheme.onSurfaceVariant),
                  ),
                )
              else
                Flexible(
                  child: ListView.builder(
                    shrinkWrap: true,
                    itemCount: lines.length,
                    itemBuilder: (context, index) {
                      final line = lines[index];
                      final lineId = line['row_id']?.toString() ?? '';
                      final body = line['body'] is Map
                          ? Map<String, dynamic>.from(line['body'] as Map)
                          : <String, dynamic>{};
                      final matched = _matched(lineId);
                      final busy = _busyLineId == lineId;
                      final title = (body['title'] ?? '').toString();
                      final pn = (body['part_number'] ?? '').toString();
                      final qty = (body['qty'] ?? 1).toString();
                      final textColor =
                          matched ? colors.success : scheme.onSurface;
                      return InkWell(
                        onTap: busy ? null : () => _match(lineId),
                        child: Padding(
                          padding: const EdgeInsets.symmetric(vertical: 6),
                          child: Row(
                            children: [
                              busy
                                  ? const SizedBox(
                                      width: 22,
                                      height: 22,
                                      child: Padding(
                                        padding: EdgeInsets.all(2),
                                        child: CircularProgressIndicator(
                                          strokeWidth: 2,
                                        ),
                                      ),
                                    )
                                  : IconButton(
                                      visualDensity: VisualDensity.compact,
                                      padding: EdgeInsets.zero,
                                      constraints: const BoxConstraints(
                                        minWidth: 28,
                                        minHeight: 28,
                                      ),
                                      tooltip: matched
                                          ? 'Уже выбрано для позиции'
                                          : 'Сделать выбранным для позиции',
                                      onPressed: () => _match(lineId),
                                      icon: Icon(
                                        matched
                                            ? Icons.star
                                            : Icons.star_border,
                                        size: 22,
                                        color: matched
                                            ? colors.success
                                            : scheme.onSurfaceVariant,
                                      ),
                                    ),
                              const SizedBox(width: 8),
                              Expanded(
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text(
                                      '$qty × $title',
                                      maxLines: 2,
                                      overflow: TextOverflow.ellipsis,
                                      style: TextStyle(color: textColor),
                                    ),
                                    if (pn.isNotEmpty)
                                      Text(
                                        pn,
                                        style: TextStyle(
                                          color: textColor,
                                          fontSize: 12,
                                        ),
                                      ),
                                  ],
                                ),
                              ),
                            ],
                          ),
                        ),
                      );
                    },
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}
