import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';

/// Totals strip for «Бюджетирование» (`ui_json.summary.kind = budget_totals`).
///
/// Metrics follow the Excel template contract (money_cache parity):
/// H = qty·price_in (закупка с НДС), M = qty·price_in·(1+markup) (продажа
/// с НДС), N = M−H (маржа), per-row VAT backs out the «без НДС» / «НДС»
/// split of the sale side.
class BudgetSummaryStrip extends StatelessWidget {
  const BudgetSummaryStrip({super.key, required this.bodies});

  /// budget_lines row bodies (raw fields: price_in / qty / vat / markup).
  final List<Map<String, dynamic>> bodies;

  static const double _vatFallback = 0.22;
  static const double _markupFallback = 0.1;
  static const double _cardRadius = 10;

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
    if (!ratio.isFinite) return '—';
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
    final qtyLabel =
        qtyTotal == qtyTotal.roundToDouble() ? qtyTotal.toInt().toString() : qtyTotal.toStringAsFixed(1);

    final tokens = context.appColors;
    final metrics = <(String, String, IconData)>[
      ('Позиций', '$count', Icons.format_list_numbered),
      ('Кол-во', qtyLabel, Icons.tag),
      ('Закупка с НДС', _money(buy), Icons.shopping_cart_outlined),
      ('Продажа с НДС', _money(sale), Icons.sell_outlined),
      ('Маржа', _money(margin), Icons.trending_up),
      ('Маржа %', _pct(marginPct), Icons.percent),
      ('Продажа без НДС', _money(saleNoVat), Icons.receipt_long_outlined),
      ('НДС', _money(vatOut), Icons.account_balance_outlined),
    ];

    return Padding(
      padding: const EdgeInsets.fromLTRB(AppSpacing.md, AppSpacing.xs, AppSpacing.md, AppSpacing.sm),
      child: Wrap(
        spacing: AppSpacing.sm,
        runSpacing: AppSpacing.xs,
        children: [
          for (final (label, value, icon) in metrics)
            Container(
              padding: const EdgeInsets.symmetric(
                horizontal: AppSpacing.md,
                vertical: AppSpacing.sm,
              ),
              decoration: BoxDecoration(
                color: tokens.surface,
                borderRadius: BorderRadius.circular(_cardRadius),
                border: Border.all(color: tokens.border),
              ),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(icon, size: 16, color: tokens.muted),
                  const SizedBox(width: AppSpacing.xs),
                  Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(
                        label,
                        style: Theme.of(context).textTheme.labelSmall?.copyWith(
                              color: tokens.muted,
                            ),
                      ),
                      Text(
                        value,
                        style: Theme.of(context)
                            .textTheme
                            .labelLarge
                            ?.copyWith(fontWeight: FontWeight.w600),
                      ),
                    ],
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }
}
