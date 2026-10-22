import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/features/meta/widgets/server_paged_add_button.dart';

/// «+» рядом с пагинатором виртуальной таблицы (мастер-прайс).
///
/// Строки мастер-прайса живут в OpenSearch, поэтому добавление товара — это
/// отдельная форма, а не inline-поле: кнопка обязана быть доступной и
/// открывать view из `ui_json.add_view`.
void main() {
  testWidgets('renders an add icon with a tooltip', (tester) async {
    var taps = 0;
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light,
        home: Scaffold(
          body: ServerPagedAddButton(onPressed: () => taps++),
        ),
      ),
    );

    expect(find.byIcon(Icons.add), findsOneWidget);
    expect(find.byTooltip('Добавить товар'), findsOneWidget);
    expect(taps, 0);
  });

  testWidgets('tap invokes the callback', (tester) async {
    var taps = 0;
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light,
        home: Scaffold(
          body: ServerPagedAddButton(onPressed: () => taps++),
        ),
      ),
    );

    await tester.tap(find.byIcon(Icons.add));
    await tester.pumpAndSettle();
    expect(taps, 1);
  });

  testWidgets('custom tooltip is honoured', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light,
        home: Scaffold(
          body: ServerPagedAddButton(
            onPressed: () {},
            tooltip: 'Новый товар',
          ),
        ),
      ),
    );
    expect(find.byTooltip('Новый товар'), findsOneWidget);
  });
}
