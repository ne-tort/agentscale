import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/features/meta/interpreters/collection_interpreter.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/features/meta/preview/seed_data_controller.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Регрессия: «Маржа» в Бюджетировании — контент «{n} ({y}%)», виден по
/// selection_only только у выделенной строки (долгий тап), не пустое поле.
void main() {
  final budgetView = {
    'slug': 'budget_lines_list',
    'kind': 'collection',
    'table_slug': 'budget_lines',
    'ui_json': {
      'version': 1,
      'kind': 'collection',
      'scaffold': {'title': {'ru': 'Бюджетирование'}},
      'title_field': 'title',
      'columns': [
        {'field': 'part_number', 'label': {'ru': 'Партномер'}},
        {'field': 'price_in', 'label': {'ru': 'Вход с НДС'}, 'align': 'end'},
        {
          'field': 'margin_total',
          'label': {'ru': 'Маржа'},
          'format': 'budget_calc',
          'variant': 'margin_total',
          'align': 'end',
          'selection_only': true,
        },
      ],
    },
  };

  ModuleMetaManifest manifest() => ModuleMetaManifest.fromJson({
        'tables': [
          {'slug': 'budget_lines'},
        ],
        'views': [budgetView],
        'seed_rows': [
          {
            'table_slug': 'budget_lines',
            'row_id': 'b1',
            'body': {
              'title': 'SSD 1TB',
              'part_number': 'ABC-1',
              'price_in': 100.0,
              'qty': 2,
              'markup': 0.1,
            },
          },
        ],
      });

  testWidgets('margin cell shows "{n} ({y}%)" only for the selected row', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        locale: const Locale('ru'),
        supportedLocales: AppLocalizations.supportedLocales,
        localizationsDelegates: const [
          AppLocalizations.delegate,
          GlobalMaterialLocalizations.delegate,
          GlobalWidgetsLocalizations.delegate,
          GlobalCupertinoLocalizations.delegate,
        ],
        home: Scaffold(
          body: CollectionViewInterpreter(
            manifest: manifest(),
            view: budgetView,
            seeds: SeedDataController(manifest()),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    // без выделения: партномер/цена видны, колонка «Маржа» скрыта целиком
    // (нет ни заголовка, ни пустой ячейки — иначе выглядит как баг)
    expect(find.text('ABC-1'), findsOneWidget);
    expect(find.text('100.0'), findsOneWidget);
    expect(find.text('20.00 (10.0%)'), findsNothing);
    expect(find.text('Маржа'), findsNothing);

    // долгий тап по строке → режим выделения → колонка появляется со значением
    await tester.longPress(find.text('SSD 1TB'));
    await tester.pumpAndSettle();
    expect(find.text('Маржа'), findsOneWidget);
    expect(find.text('20.00 (10.0%)'), findsOneWidget);
  });
}
