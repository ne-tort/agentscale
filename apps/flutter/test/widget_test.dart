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

  testWidgets('app entry shows login form (default ru)', (tester) async {
    SharedPreferences.setMockInitialValues({});
    await tester.pumpWidget(const ProdavanApp());
    await tester.pumpAndSettle();
    expect(find.text('Вход'), findsWidgets);
    expect(find.text('Логин'), findsOneWidget);
    expect(find.text('Пароль'), findsOneWidget);
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

  testWidgets('entity collection applies row color and bold title', (tester) async {
    const warning = Color(0xFFFF9800);
    await tester.pumpWidget(
      themed(
        Builder(
          builder: (context) => AppScaffold(
            title: const Text('T'),
            body: AppEntityCollection(
              mode: AppEntityCollectionMode.table,
              rows: const [
                AppEntityRow(
                  id: '1',
                  title: 'Bold row',
                  rowColor: warning,
                  titleBold: true,
                  cells: {'x': 'cell'},
                ),
              ],
              primaryColumnLabel: 'Name',
              columns: [AppEntityColumn(id: 'x', label: 'X')],
              onOpen: (_) {},
            ),
          ),
        ),
      ),
    );
    final title = tester.widget<Text>(find.text('Bold row'));
    expect(title.style?.fontWeight, FontWeight.w600);
    expect(title.style?.color, warning);
    final cell = tester.widget<Text>(find.text('cell'));
    expect(cell.style?.color, warning);
  });

  testWidgets('entity collection table mode scrolls when rows exceed height', (tester) async {
    final rows = List.generate(
      24,
      (i) => AppEntityRow(
        id: '$i',
        title: 'ai-key-$i',
        cells: const {'x': 'cell'},
      ),
    );
    await tester.binding.setSurfaceSize(const Size(900, 220));
    addTearDown(() => tester.binding.setSurfaceSize(null));

    await tester.pumpWidget(
      themed(
        AppScaffold(
          body: AppEntityCollection(
            mode: AppEntityCollectionMode.table,
            rows: rows,
            primaryColumnLabel: 'Key',
            columns: const [AppEntityColumn(id: 'x', label: 'Type')],
            onOpen: (_) {},
          ),
        ),
      ),
    );

    expect(find.byType(SingleChildScrollView), findsWidgets);
    expect(tester.takeException(), isNull);
  });

  testWidgets('entity collection rowActions show inline and tap deselects', (tester) async {
    var opened = false;
    var actionPressed = false;
    await tester.binding.setSurfaceSize(const Size(900, 400));
    addTearDown(() => tester.binding.setSurfaceSize(null));

    await tester.pumpWidget(
      themed(
        AppScaffold(
          body: AppEntityCollection(
            mode: AppEntityCollectionMode.table,
            rows: const [
              AppEntityRow(id: 'file.txt', title: 'readme.txt', cells: {'size': '12 B'}),
            ],
            primaryColumnLabel: 'Name',
            columns: const [AppEntityColumn(id: 'size', label: 'Size', width: 72)],
            onOpen: (_) => opened = true,
            rowActions: [
              AppEntityRowAction(
                icon: Icons.download_outlined,
                tooltip: 'Download',
                onPressed: (_) async {
                  actionPressed = true;
                },
              ),
            ],
          ),
        ),
      ),
    );

    expect(find.byIcon(Icons.download_outlined), findsNothing);

    await tester.longPress(find.text('readme.txt'));
    await tester.pumpAndSettle();

    expect(find.byIcon(Icons.download_outlined), findsOneWidget);
    expect(opened, isFalse);

    await tester.tap(find.text('readme.txt'));
    await tester.pumpAndSettle();

    expect(find.byIcon(Icons.download_outlined), findsNothing);
    expect(opened, isFalse);
    expect(actionPressed, isFalse);
  });

  testWidgets('entity collection rowActions respect visible predicate', (tester) async {
    await tester.binding.setSurfaceSize(const Size(900, 400));
    addTearDown(() => tester.binding.setSurfaceSize(null));

    await tester.pumpWidget(
      themed(
        AppScaffold(
          body: AppEntityCollection(
            mode: AppEntityCollectionMode.table,
            rows: const [
              AppEntityRow(id: 'dir', title: 'docs', cells: {'size': '—'}),
            ],
            primaryColumnLabel: 'Name',
            columns: const [AppEntityColumn(id: 'size', label: 'Size', width: 72)],
            onOpen: (_) {},
            rowActions: [
              AppEntityRowAction(
                icon: Icons.download_outlined,
                tooltip: 'Download',
                visible: (row) => row.id != 'dir',
                onPressed: (_) async {},
              ),
            ],
          ),
        ),
      ),
    );

    await tester.longPress(find.text('docs'));
    await tester.pumpAndSettle();

    expect(find.byIcon(Icons.download_outlined), findsNothing);
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
