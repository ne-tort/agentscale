import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/features/meta/interpreters/collection_interpreter.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/features/meta/preview/seed_data_controller.dart';
import 'package:prodavan/l10n/app_localizations.dart';

Widget _ruApp(Widget home) => MaterialApp(
      theme: AppTheme.light,
      locale: const Locale('ru'),
      supportedLocales: AppLocalizations.supportedLocales,
      localizationsDelegates: const [
        AppLocalizations.delegate,
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      home: Scaffold(body: home),
    );

/// Пикер компонента из пула группы: фильтр по group_id+class_key из контекстной
/// строки (слота сборки), выбор пишет `item_id` на слот.
Map<String, dynamic> _manifest() => {
      'syntax_version': 1,
      'tables': [
        {'slug': 'ready_build_slots', 'label': 'Слоты', 'storage_kind': 'json_document', 'enabled': true},
        {'slug': 'build_group_items', 'label': 'Пул', 'storage_kind': 'json_document', 'enabled': true},
      ],
      'columns': [
        {'table_slug': 'ready_build_slots', 'name': 'item_id', 'label': 'Компонент', 'type': 'text'},
        {'table_slug': 'ready_build_slots', 'name': 'build_id', 'label': 'Сборка', 'type': 'text'},
        {'table_slug': 'ready_build_slots', 'name': 'group_id', 'label': 'Группа', 'type': 'text'},
        {'table_slug': 'ready_build_slots', 'name': 'class_key', 'label': 'Класс', 'type': 'text'},
        {'table_slug': 'build_group_items', 'name': 'best_title', 'label': 'Товар', 'type': 'text'},
        {'table_slug': 'build_group_items', 'name': 'part_number', 'label': 'P/N', 'type': 'text'},
        {'table_slug': 'build_group_items', 'name': 'group_id', 'label': 'Группа', 'type': 'text'},
        {'table_slug': 'build_group_items', 'name': 'class_key', 'label': 'Класс', 'type': 'text'},
        {'table_slug': 'build_group_items', 'name': 'best_price', 'label': 'Цена', 'type': 'number'},
      ],
      'views': [
        {
          'slug': 'build_group_items_pick',
          'table_slug': 'build_group_items',
          'kind': 'collection',
          'ui_json': {
            'version': 1,
            'kind': 'collection',
            'title_field': 'best_title',
            'subtitle_fields': ['class_label', 'part_number'],
            'row_filter_from_context': {'group_id': 'group_id', 'class_key': 'class_key'},
            'columns': [
              {'field': 'best_title', 'label': 'Товар'},
              {'field': 'best_price', 'label': 'Цена'},
            ],
            'sort': [
              {'field': 'best_price', 'dir': 'asc'},
            ],
            'selection': {
              'kind': 'single',
              'control': 'switch',
              'placement': 'trailing',
              'disable_row_tap': true,
              'match_context_field': 'item_id',
              'set_on_context': {
                'field': 'item_id',
                'value_from': 'row_id',
                'pop_after': true,
              },
            },
          },
        },
      ],
      'seed_rows': [
        {
          'table_slug': 'ready_build_slots',
          'row_id': 'slot_ram',
          'body': {
            'build_id': 'b1',
            'group_id': 'grp_1',
            'type_id': 'etype_ram',
            'class_key': 'ram_16',
            'item_id': 'it_kingston',
          },
        },
        // два кандидата одного класса и группы
        {
          'table_slug': 'build_group_items',
          'row_id': 'it_kingston',
          'body': {'best_title': 'Kingston 16GB 3200', 'part_number': 'KF432C16BB/16', 'group_id': 'grp_1', 'class_key': 'ram_16', 'best_price': 3701.4},
        },
        {
          'table_slug': 'build_group_items',
          'row_id': 'it_corsair',
          'body': {'best_title': 'Corsair 16GB 3000', 'part_number': 'CMK16GX4M2D3000C16', 'group_id': 'grp_1', 'class_key': 'ram_16', 'best_price': 3831.6},
        },
        // другой класс той же группы — не должен показываться
        {
          'table_slug': 'build_group_items',
          'row_id': 'it_cpu',
          'body': {'best_title': 'Intel i5-12400F', 'part_number': 'CM8071504553416', 'group_id': 'grp_1', 'class_key': 'cpu_6c', 'best_price': 11532.0},
        },
        // тот же класс, но ДРУГАЯ группа — не должен показываться
        {
          'table_slug': 'build_group_items',
          'row_id': 'it_ghost',
          'body': {'best_title': 'Другая группа 16GB', 'part_number': 'X', 'group_id': 'grp_2', 'class_key': 'ram_16', 'best_price': 1.0},
        },
      ],
    };

void main() {
  testWidgets(
    'пикер пула фильтрует по группе+классу слота и пишет item_id в строку слота',
    (tester) async {
      tester.view.physicalSize = const Size(500, 900);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);

      final manifest = ModuleMetaManifest.fromJson(_manifest());
      final seeds = SeedDataController(manifest);
      final view = manifest.viewBySlug('build_group_items_pick')!;

      await tester.pumpWidget(
        _ruApp(
          CollectionViewInterpreter(
            manifest: manifest,
            view: view,
            seeds: seeds,
            contextRowId: 'slot_ram',
          ),
        ),
      );
      await tester.pumpAndSettle();

      // только кандидаты той же группы и того же класса
      expect(find.text('Kingston 16GB 3200'), findsOneWidget);
      expect(find.text('Corsair 16GB 3000'), findsOneWidget);
      expect(find.text('Intel i5-12400F'), findsNothing);
      expect(find.text('Другая группа 16GB'), findsNothing);

      // выбираем более дорогую Corsair (второй switch)
      final switches = tester.widgetList<Switch>(find.byType(Switch)).toList();
      expect(switches.length, 2);
      await tester.tap(find.byType(Switch).at(1));
      await tester.pumpAndSettle();

      // запись пошла в строку слота (item_id), а не в карту на сборке
      final slot = seeds.bodyFor('slot_ram');
      expect(slot['item_id'], 'it_corsair');
    },
  );

  testWidgets('текущий выбор слота подсвечивается в пикере', (tester) async {
    tester.view.physicalSize = const Size(500, 900);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final manifest = ModuleMetaManifest.fromJson(_manifest());
    final seeds = SeedDataController(manifest);
    final view = manifest.viewBySlug('build_group_items_pick')!;

    await tester.pumpWidget(
      _ruApp(
        CollectionViewInterpreter(
          manifest: manifest,
          view: view,
          seeds: seeds,
          contextRowId: 'slot_ram',
        ),
      ),
    );
    await tester.pumpAndSettle();

    final switches = tester.widgetList<Switch>(find.byType(Switch)).toList();
    // текущий item_id слота = it_kingston — его switch включён
    expect(switches.where((s) => s.value).length, 1);
  });
}
