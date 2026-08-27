import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:prodavan/app.dart';
import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_catalog_select_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

Widget themed(Widget home, {Locale locale = const Locale('en')}) {
  return MaterialApp(
    theme: AppTheme.light,
    locale: locale,
    supportedLocales: AppLocalizations.supportedLocales,
    localizationsDelegates: const [
      AppLocalizations.delegate,
      GlobalMaterialLocalizations.delegate,
      GlobalWidgetsLocalizations.delegate,
      GlobalCupertinoLocalizations.delegate,
    ],
    home: home,
  );
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('app entry shows login fields (default ru)', (tester) async {
    SharedPreferences.setMockInitialValues({});
    await tester.pumpWidget(const ProdavanApp());
    // Wait for appSettings.load() + session restore → LoginPage.
    await tester.pumpAndSettle();
    expect(find.byType(TextField), findsNWidgets(2));
    expect(find.byIcon(Icons.arrow_forward_rounded), findsOneWidget);
  });

  testWidgets('entity collection list opens row', (tester) async {
    String? opened;
    await tester.pumpWidget(
      themed(
        AppScaffold(
          title: const Text('T'),
          body: AppEntityCollection(
            mode: AppEntityCollectionMode.list,
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

  testWidgets('catalog select page multi select', (tester) async {
    await tester.pumpWidget(
      themed(
        AppCatalogSelectPage(
          title: 'Pick',
          multiSelect: true,
          items: const [
            AppCatalogSelectItem(id: 'a', title: 'One'),
            AppCatalogSelectItem(id: 'b', title: 'Two'),
          ],
        ),
      ),
    );
    expect(find.text('One'), findsOneWidget);
    expect(find.text('Two'), findsOneWidget);
  });
}
