import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';

/// Totals table for «Бюджетирование» (`ui_json.summary.kind = budget_totals`).
///
/// Renders as a real summary TABLE (one row per key budget column with the
/// aggregate value) above the budget collection.
///
/// Math follows the Excel template contract (money_cache parity):
/// H = qty·price_in (закупка с НДС), M = qty·price_in·(1+markup) (продажа
/// с НДС), N = M−H (маржа); per-row VAT backs out the «без НДС» / «НДС»
/// split of the sale side.
class BudgetSummaryStrip extends StatelessWidget {
  const BudgetSummaryStrip({super.key, required this.bodies});

  /// budget_lines row bodies (raw fields: price_in / qty / vat / markup).
  final List<Map<String, dynamic>> bodies;

  static const double _vatFallback = 0.22;
  static const double _markupFallback = 0.1;

  static double _num(dynamic raw, double fallback) {
    if (raw is num) return raw.toDouble();
    if (raw is String) return double.tryParse(raw.trim()) ?? fallback;
    return fallback;
  }

  String _money(double v) {
    final fixed = v.abs() < 0.005 ? '0.00' : v.toStringAsFixed(2);
    final parts = fixed.split('.');
    final grouped = parts[0].replaceAllMapped(
      RegExp(r'^(-?\d{1,3})(?=(\d{3})+$)'),
      (m) => '${m[1]} ',
    );
    return '$grouped,${parts[1]} ₽';
  }

  String _pct(double ratio) {
    if (!ratio.isFinite) return '-,0 %';
    return '${(ratio * 100).toStringAsFixed(1).replaceAll('.', ',')} %';
  }

  @override
  Widget build(BuildContext context) {
    var buy = 0.0;
    var sale = 0.0;
    var saleNoVat = 0.0;
    var vatOut = 0.0;
    var qtyTotal = 0.0;
    var count = 0;
    for (final b in bodies) {
      final priceIn = _num(b['price_in'], 0);
      if (priceIn <= 0) continue;
      count += 1;
      final qty = _num(b['qty'], 1);
      final markup = _num(b['markup'], _markupFallback);
      final vat = _num(b['vat'], _vatFallback);
      buy += qty * priceIn;
      final rowSale = (qty * priceIn * (1 + markup)).toDouble();
      sale += rowSale;
      final denom = 1 + vat;
      final rowNoVat = denom > 0 ? rowSale / denom : rowSale;
      saleNoVat += rowNoVat;
      vatOut += rowSale - rowNoVat;
      qtyTotal += qty;
    }
    final margin = sale - buy;
    final marginPct = sale > 0 ? margin / sale : 0.0;
    final qtyLabel = qtyTotal == qtyTotal.roundToDouble()
        ? qtyTotal.toInt().toString()
        : qtyTotal.toStringAsFixed(1);

    final tokens = context.appColors;
    final theme = Theme.of(context);
    final rows = <(String, String)>[
      ('Позиций', '$count'),
      ('Кол-во', qtyLabel),
      ('Закупка с НДС', _money(buy)),
      ('Продажа с НДС', _money(sale)),
      ('Маржа', _money(margin)),
      ('Маржа %', _pct(marginPct)),
      ('Продажа без НДС', _money(saleNoVat)),
      ('НДС', _money(vatOut)),
    ];

    return Padding(
      padding: const EdgeInsets.fromLTRB(AppSpacing.md, AppSpacing.xs, AppSpacing.md, AppSpacing.sm),
      child: Container(
        decoration: BoxDecoration(
          color: tokens.surface,
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: tokens.border),
        ),
        child: ClipRRect(
          borderRadius: BorderRadius.circular(10),
          child: Table(
            defaultVerticalAlignment: TableCellVerticalAlignment.middle,
            columnWidths: const {
              0: FlexColumnWidth(1),
              1: FlexColumnWidth(1),
            },
            border: TableBorder(
              horizontalInside: BorderSide(color: tokens.border.withValues(alpha: 0.6)),
            ),
            children: [
              TableRow(
                decoration: BoxDecoration(color: tokens.surfaceContainer),
                children: [
                  _cell('Показатель', theme, tokens, header: true),
                  _cell('Значение', theme, tokens, header: true, alignRight: true),
                ],
              ),
              for (final (label, value) in rows)
                TableRow(
                  children: [
                    _cell(label, theme, tokens),
                    _cell(value, theme, tokens, strong: true, alignRight: true),
                  ],
                ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _cell(
    String text,
    ThemeData theme,
    AppColorTokens tokens, {
    bool header = false,
    bool strong = false,
    bool alignRight = false,
  }) {
    final style = header
        ? theme.textTheme.labelMedium?.copyWith(
            color: tokens.muted,
            fontWeight: FontWeight.w600,
          )
        : theme.textTheme.bodyMedium?.copyWith(
            fontWeight: strong ? FontWeight.w600 : FontWeight.w400,
          );
    return Padding(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.md,
        vertical: AppSpacing.sm,
      ),
      child: Text(
        text,
        style: style,
        textAlign: alignRight ? TextAlign.right : TextAlign.left,
      ),
    );
  }
}
