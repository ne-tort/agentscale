import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/l10n/app_localizations.dart';

Widget _wrap(Widget child) {
  return MaterialApp(
    supportedLocales: AppLocalizations.supportedLocales,
    localizationsDelegates: const [
      AppLocalizations.delegate,
      GlobalMaterialLocalizations.delegate,
      GlobalWidgetsLocalizations.delegate,
      GlobalCupertinoLocalizations.delegate,
    ],
    home: Scaffold(body: child),
  );
}

void main() {
  testWidgets('selection_only column: empty until long-press, value after', (tester) async {
    final rows = [
      const AppEntityRow(
        id: 'r1',
        title: 'SSD 1TB',
        cells: {'price_in': '100.00', 'margin_total': '20.00 (10.0%)'},
      ),
    ];
    await tester.pumpWidget(
      _wrap(
        AppEntityCollection(
          mode: AppEntityCollectionMode.table,
          rows: rows,
          primaryColumnLabel: 'Наименование',
          columns: const [
            AppEntityColumn(id: 'price_in', label: 'Вход'),
            AppEntityColumn(
              id: 'margin_total',
              label: 'Маржа',
              selectionOnly: true,
            ),
          ],
          onOpen: (_) {},
          onDelete: (_) async {},
        ),
      ),
    );
    await tester.pumpAndSettle();
    // По умолчанию ячейка скрыта
    expect(find.text('20.00 (10.0%)'), findsNothing);
    expect(find.text('100.00'), findsOneWidget);

    // Долгий тап по строке → режим выделения → ячейка появляется
    await tester.longPress(find.text('SSD 1TB'));
    await tester.pumpAndSettle();
    expect(find.text('20.00 (10.0%)'), findsOneWidget);
  });
}
