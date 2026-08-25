import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:prodavan/app.dart';
import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_selector_page.dart';

Widget themed(Widget home) {
  return MaterialApp(theme: AppTheme.light, home: home);
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('app entry shows sign in', (tester) async {
    SharedPreferences.setMockInitialValues({});
    await tester.pumpWidget(const ProdavanApp());
    await tester.pump(); // SessionGate starts restore
    await tester.pump(); // load() completes → LoginPage
    expect(find.text('Sign in'), findsOneWidget);
  });

  testWidgets('entity collection list opens row', (tester) async {
    String? opened;
    await tester.pumpWidget(
      themed(
        AppScaffold(
          title: const Text('T'),
          body: AppEntityCollection(
            initialMode: AppEntityCollectionMode.list,
            allowModeToggle: false,
            rows: const [
              AppEntityRow(id: '1', title: 'Row A', cells: {}),
            ],
            columns: const [],
            onOpen: (r) => opened = r.id,
          ),
        ),
      ),
    );
    await tester.tap(find.text('Row A'));
    expect(opened, '1');
  });

  testWidgets('selector page multi select', (tester) async {
    await tester.pumpWidget(
      themed(
        AppSelectorPage(
          title: 'Pick',
          multiSelect: true,
          showCheckboxes: true,
          items: const [
            AppSelectorItem(id: 'a', title: 'One'),
            AppSelectorItem(id: 'b', title: 'Two'),
          ],
        ),
      ),
    );
    expect(find.text('One'), findsOneWidget);
    expect(find.text('Two'), findsOneWidget);
  });
}
