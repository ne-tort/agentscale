import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/features/meta/module_cell_format.dart';
import 'package:prodavan/features/meta/widgets/budget_summary_strip.dart';
import 'package:prodavan/features/meta/widgets/editable_number_cell.dart';

void main() {
  group('BudgetSummaryStrip (ui_json.summary kind budget_totals)', () {
    testWidgets('renders metric cards from budget bodies', (tester) async {
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(
            body: BudgetSummaryStrip(
              bodies: [
                {'price_in': 15000, 'qty': 2, 'vat': 0.22, 'markup': 0.1},
                {'price_in': 250.5, 'qty': 10, 'vat': 0.22, 'markup': 0.2},
                {'price_in': 0, 'qty': 1, 'vat': 0.22, 'markup': 0.1},
              ],
            ),
          ),
        ),
      );
      expect(find.text('Позиций'), findsOneWidget);
      expect(find.text('2'), findsOneWidget); // priced rows only
      // Закупка с НДС: 2*15000 + 10*250.5 = 32 505
      expect(find.text('32 505,00 ₽'), findsOneWidget);
      // Продажа с НДС: 33000 + 3006 = 36 006
      expect(find.text('36 006,00 ₽'), findsOneWidget);
      // Маржа: 3 501
      expect(find.text('3 501,00 ₽'), findsOneWidget);
      expect(find.text('9,7 %'), findsOneWidget);
      expect(find.text('Продажа без НДС'), findsOneWidget);
      expect(find.text('НДС'), findsOneWidget);
    });

    testWidgets('empty bodies render zero metrics', (tester) async {
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(body: BudgetSummaryStrip(bodies: [])),
        ),
      );
      expect(find.text('0,00 ₽'), findsNWidgets(5));
      expect(find.text('Позиций'), findsOneWidget);
      expect(find.text('0,0 %'), findsOneWidget); // margin % with zero sale
    });
  });

  group('EditableNumberCell', () {
    testWidgets('tap opens editor, Enter submits parsed value', (tester) async {
      final submitted = <num>[];
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: EditableNumberCell(
              value: 0.22,
              onSubmit: (parsed) async => submitted.add(parsed),
            ),
          ),
        ),
      );
      expect(find.text('0.22'), findsOneWidget);
      expect(find.byType(TextField), findsNothing);

      await tester.tap(find.byType(EditableNumberCell));
      await tester.pumpAndSettle();
      expect(find.byType(TextField), findsOneWidget);

      await tester.enterText(find.byType(TextField), '0,3');
      await tester.testTextInput.receiveAction(TextInputAction.done);
      await tester.pumpAndSettle();
      expect(submitted, [0.3]);
      expect(find.byType(TextField), findsNothing);
    });

    testWidgets('invalid text cancels without submit', (tester) async {
      final submitted = <num>[];
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: EditableNumberCell(
              value: 1,
              onSubmit: (parsed) async => submitted.add(parsed),
            ),
          ),
        ),
      );
      await tester.tap(find.byType(EditableNumberCell));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField), 'abc');
      await tester.testTextInput.receiveAction(TextInputAction.done);
      await tester.pumpAndSettle();
      expect(submitted, isEmpty);
      expect(find.byType(TextField), findsNothing);
      expect(find.text('1'), findsOneWidget);
    });
  });

  group('module cell formats (templates module)', () {
    test('file_name extracts FileRef filename', () {
      final out = formatModuleCell(
        item: {'row_id': 't1'},
        body: {
          'file': {
            'filename': 'my-kp-template.xlsx',
            'size': 2048,
            'asset_id': 'a1',
          },
        },
        col: {'field': 'file', 'format': 'file_name'},
        tableSlug: 'templates',
      );
      expect(out, 'my-kp-template.xlsx');
    });

    test('file_name with plain value renders empty', () {
      final out = formatModuleCell(
        item: {'row_id': 't1'},
        body: {'file': null},
        col: {'field': 'file', 'format': 'file_name'},
        tableSlug: 'templates',
      );
      expect(out, '');
    });

    test('bool_yes_no labels', () {
      String cell(dynamic value) => formatModuleCell(
            item: {'row_id': 't1'},
            body: {'active': value},
            col: {'field': 'active', 'format': 'bool_yes_no'},
            tableSlug: 'templates',
          );
      expect(cell(true), 'Да');
      expect(cell(false), 'Нет');
    });
  });
}
