import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/l10n/app_localizations.dart';

Widget _host(Widget child) => MaterialApp(
      localizationsDelegates: AppLocalizations.localizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      locale: const Locale('ru'),
      home: Scaffold(body: Center(child: child)),
    );

Finder _horizontalScrollable() => find.byWidgetPredicate((w) {
      if (w is! Scrollable) return false;
      return w.axisDirection == AxisDirection.right;
    });

void main() {
  testWidgets('wide table is horizontally scrollable on narrow viewport', (tester) async {
    await tester.pumpWidget(_host(AppEntityCollection(
      columns: [
        AppEntityColumn(id: 'title', label: 'Товар', maxWidth: 400),
        AppEntityColumn(id: 'pn', label: 'Партномер', maxWidth: 200),
        AppEntityColumn(id: 'qty', label: 'Кол-во', maxWidth: 120),
        AppEntityColumn(id: 'price', label: 'Цена (руб.)', maxWidth: 140),
        AppEntityColumn(id: 'vat', label: 'НДС', maxWidth: 140),
      ],
      rows: const [
        AppEntityRow(
          id: 'r1',
          title: 'row',
          cells: {
            'title': 'SSD Samsung 990 Pro 2TB NVMe very long title here',
            'pn': 'MZ-77Q2T0BW',
            'qty': '2',
            'price': '12500.00',
            'vat': '0.22',
          },
        ),
      ],
      onOpen: (_) {},
    )));
    await tester.pumpAndSettle();

    expect(_horizontalScrollable(), findsWidgets,
        reason: 'no horizontal scrollable found');
    final state = tester.state<ScrollableState>(_horizontalScrollable().first);
    expect(state.position.maxScrollExtent, greaterThan(0),
        reason: 'table fits the viewport: no horizontal scroll available');
  });

  testWidgets('narrow table fills viewport without scroll', (tester) async {
    await tester.pumpWidget(_host(AppEntityCollection(
      columns: [
        AppEntityColumn(id: 'a', label: 'A', maxWidth: 100),
        AppEntityColumn(id: 'b', label: 'B', maxWidth: 100),
      ],
      rows: const [AppEntityRow(id: 'r', title: 'row', cells: {'a': 'x', 'b': 'y'})],
      onOpen: (_) {},
    )));
    await tester.pumpAndSettle();

    final state = tester.state<ScrollableState>(_horizontalScrollable().first);
    expect(state.position.maxScrollExtent, 0,
        reason: 'narrow table should not scroll horizontally');
  });
}
