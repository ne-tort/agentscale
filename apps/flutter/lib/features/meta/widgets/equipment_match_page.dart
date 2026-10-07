import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/features/meta/runtime/runtime_data_adapter.dart';

/// Страница сопоставления найденного товара (OS src_hash) с позицией заказчика.
///
/// Открывается тапом по товару на «Поиске товаров» и «Мастер прайсе».
/// Таблица позиций заказчика: первая колонка — звезда (заполнена, когда
/// товар уже выбран для позиции); тап по строке/звезде сопоставляет товар
/// (экшен match_product → выбранный оффер позиции); успех — зелёным.
class EquipmentMatchPage extends StatefulWidget {
  const EquipmentMatchPage({
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

  @override
  State<EquipmentMatchPage> createState() => _EquipmentMatchPageState();
}

class _EquipmentMatchPageState extends State<EquipmentMatchPage> {
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
    return Scaffold(
      appBar: AppBar(
        title: const Text('Сопоставить с позицией'),
      ),
      body: AnimatedBuilder(
        animation: widget.adapter,
        builder: (context, _) {
          final lines = _lines;
          if (lines.isEmpty) {
            return Center(
              child: Text(
                'В текущем чате нет позиций заказчика',
                style: TextStyle(color: scheme.onSurfaceVariant),
              ),
            );
          }
          return Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              if (widget.productTitle.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
                  child: Text(
                    widget.productTitle,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(color: scheme.onSurfaceVariant),
                  ),
                ),
              const Divider(height: 1),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
                child: Row(
                  children: [
                    const SizedBox(width: 40),
                    Expanded(
                      flex: 3,
                      child: Text('Позиция', style: _headerStyle(context)),
                    ),
                    Expanded(
                      flex: 2,
                      child: Text('Партномер', style: _headerStyle(context)),
                    ),
                    SizedBox(
                      width: 70,
                      child: Text(
                        'Кол-во',
                        style: _headerStyle(context),
                        textAlign: TextAlign.end,
                      ),
                    ),
                  ],
                ),
              ),
              const Divider(height: 1),
              Expanded(
                child: ListView.separated(
                  itemCount: lines.length,
                  separatorBuilder: (_, __) => const Divider(height: 1),
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
                        padding: const EdgeInsets.symmetric(
                          horizontal: 8,
                          vertical: 10,
                        ),
                        child: Row(
                          children: [
                            SizedBox(
                              width: 40,
                              child: busy
                                  ? const SizedBox(
                                      width: 20,
                                      height: 20,
                                      child: Padding(
                                        padding: EdgeInsets.all(1),
                                        child: CircularProgressIndicator(
                                          strokeWidth: 2,
                                        ),
                                      ),
                                    )
                                  : Icon(
                                      matched
                                          ? Icons.star
                                          : Icons.star_border,
                                      size: 22,
                                      color: matched
                                          ? colors.success
                                          : scheme.onSurfaceVariant,
                                    ),
                            ),
                            Expanded(
                              flex: 3,
                              child: Text(
                                title.isEmpty ? '—' : title,
                                maxLines: 2,
                                overflow: TextOverflow.ellipsis,
                                style: TextStyle(color: textColor),
                              ),
                            ),
                            Expanded(
                              flex: 2,
                              child: Text(
                                pn.isEmpty ? '—' : pn,
                                maxLines: 2,
                                overflow: TextOverflow.ellipsis,
                                style: TextStyle(color: textColor),
                              ),
                            ),
                            SizedBox(
                              width: 70,
                              child: Text(
                                qty,
                                textAlign: TextAlign.end,
                                style: TextStyle(color: textColor),
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
          );
        },
      ),
    );
  }

  TextStyle _headerStyle(BuildContext context) => TextStyle(
        fontWeight: FontWeight.w600,
        color: Theme.of(context).colorScheme.onSurfaceVariant,
        fontSize: 12,
      );
}