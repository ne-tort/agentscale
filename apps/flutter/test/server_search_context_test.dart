import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/features/meta/widgets/server_search_context.dart';

void main() {
  testWidgets('shows active query and stock filter in result context', (tester) async {
    var queryCleared = false;
    var stockCleared = false;
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ServerSearchContext(
            query: 'RTX 5070',
            inStockOnly: true,
            onClearQuery: () => queryCleared = true,
            onShowAllStock: () => stockCleared = true,
          ),
        ),
      ),
    );

    expect(find.text('RTX 5070'), findsOneWidget);
    expect(find.text('Только в наличии'), findsOneWidget);
    await tester.tap(find.byIcon(Icons.cancel).first);
    await tester.pump();
    expect(queryCleared, isTrue);
    await tester.tap(find.byIcon(Icons.cancel).last);
    await tester.pump();
    expect(stockCleared, isTrue);
  });

  testWidgets('hides context when no server filters are active', (tester) async {
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(
          body: ServerSearchContext(
            query: '',
            inStockOnly: false,
            onClearQuery: _noop,
            onShowAllStock: _noop,
          ),
        ),
      ),
    );

    expect(find.byType(InputChip), findsNothing);
    expect(find.text('Только в наличии'), findsNothing);
  });
}

void _noop() {}
