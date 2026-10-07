import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/features/meta/module_cell_format.dart';

void main() {
  group('formatBudgetCalcCell (format: budget_calc)', () {
    String cell(Map<String, dynamic> body, Map<String, dynamic> col) {
      return formatModuleCell(
        item: {'row_id': 'bl1'},
        body: body,
        col: col,
        tableSlug: 'budget_lines',
      );
    }

    final priceOutCol = {
      'field': 'price_out',
      'format': 'budget_calc',
      'variant': 'price_out',
    };
    final marginTotalCol = {
      'field': 'margin_total',
      'format': 'budget_calc',
      'variant': 'margin_total',
    };

    test('price_out = price_in * (1 + markup)', () {
      expect(
        cell({'price_in': 100.0, 'qty': 2, 'markup': 0.25}, priceOutCol),
        '125.00',
      );
    });

    test('margin_total = {qty * (price_out - price_in)} ({markup %})', () {
      expect(
        cell({'price_in': 100.0, 'qty': 3, 'markup': 0.25}, marginTotalCol),
        '75.00 (25.0%)',
      );
    });

    test('default markup 0.1 when absent', () {
      expect(cell({'price_in': 100.0}, priceOutCol), '110.00');
      expect(cell({'price_in': 100.0, 'qty': 2}, marginTotalCol), '20.00 (10.0%)');
    });

    test('default qty 1 when absent', () {
      expect(cell({'price_in': 100.0, 'markup': 0.5}, marginTotalCol), '50.00 (50.0%)');
    });

    test('vat (default 0.22) does not change the canonical formulas', () {
      final body = {'price_in': 100.0, 'qty': 2, 'vat': 0.22, 'markup': 0.1};
      expect(cell(body, priceOutCol), '110.00');
      expect(cell(body, marginTotalCol), '20.00 (10.0%)');
      // Explicit zero VAT changes nothing either — price_in is with VAT.
      expect(cell({...body, 'vat': 0.0}, priceOutCol), '110.00');
    });

    test('missing price_in renders empty string', () {
      expect(cell({'qty': 2, 'markup': 0.3}, priceOutCol), '');
      expect(cell({'qty': 2}, marginTotalCol), '');
      expect(cell({}, marginTotalCol), '');
    });

    test('stringified numbers are parsed', () {
      expect(
        cell({'price_in': '200', 'qty': '2', 'markup': '0.5'}, priceOutCol),
        '300.00',
      );
      expect(
        cell({'price_in': '200', 'qty': '2', 'markup': '0.5'}, marginTotalCol),
        '200.00 (50.0%)',
      );
    });

    test('integer price_in renders fixed two decimals', () {
      expect(cell({'price_in': 1000, 'markup': 0.1}, priceOutCol), '1100.00');
    });
  });

  group('offer_price format', () {
    test('null price renders «Нет цены», numeric renders as-is', () {
      const col = {'field': 'price', 'format': 'offer_price'};
      expect(
        formatModuleCell(item: const {}, body: const {'price': null}, col: col, tableSlug: 'found_offers'),
        'Нет цены',
      );
      expect(
        formatModuleCell(item: const {}, body: const {'price': 99.5}, col: col, tableSlug: 'found_offers'),
        '99.5',
      );
    });
  });

  group('budgetPriceInLabel', () {
    test('no price / no price + on order / priced on order', () {
      expect(budgetPriceInLabel({'price_in': null, 'on_order': false}), 'Нет цены');
      expect(budgetPriceInLabel({'on_order': true}), 'Под заказ');
      expect(
        budgetPriceInLabel({'price_in': 34696.91, 'on_order': true}),
        '34696.91 (Под заказ)',
      );
      expect(budgetPriceInLabel({'price_in': 100.0, 'on_order': false}), isNull);
      expect(budgetPriceInLabel({'price_in': 100.0}), isNull);
    });
  });
}
