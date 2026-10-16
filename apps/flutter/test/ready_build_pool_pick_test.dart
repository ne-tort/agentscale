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

/// WAVE12: пикер карточки в слот готовой сборки. Контекст — открытый слот:
/// карточки фильтруются по типу слота (type_id) и покрытию требований
/// (row_matches_context_map: attrs карточки ⊇ constraints слота), выбор пишет
/// `card_id` на слот. Цена в списке — лидер по партномеру (best_price).
Map<String, dynamic> _manifest() => {
      'syntax_version': 1,
      'tables': [
        {'slug': 'ready_build_slots', 'label': 'Слоты', 'storage_kind': 'json_document', 'enabled': true},
        {'slug': 'part_cards', 'label': 'Карточки', 'storage_kind': 'json_document', 'enabled': true},
      ],
      'columns': [
        {'table_slug': 'ready_build_slots', 'name': 'card_id', 'label': 'Карточка', 'type': 'text'},
        {'table_slug': 'ready_build_slots', 'name': 'type_id', 'label': 'Тип', 'type': 'text'},
        {'table_slug': 'ready_build_slots', 'name': 'constraints', 'label': 'Требования', 'type': 'json'},
        {'table_slug': 'part_cards', 'name': 'best_title', 'label': 'Товар', 'type': 'text'},
        {'table_slug': 'part_cards', 'name': 'part_number', 'label': 'P/N', 'type': 'text'},
        {'table_slug': 'part_cards', 'name': 'type_id', 'label': 'Тип', 'type': 'text'},
        {'table_slug': 'part_cards', 'name': 'attrs', 'label': 'Характеристики', 'type': 'json'},
        {'table_slug': 'part_cards', 'name': 'best_price', 'label': 'Цена', 'type': 'number'},
      ],
      'views': [
        {
          'slug': 'part_cards_pick',
          'table_slug': 'part_cards',
          'kind': 'collection',
          'ui_json': {
            'version': 1,
            'kind': 'collection',
            'title_field': 'best_title',
            'subtitle_fields': ['attrs_label', 'part_number'],
            'row_filter_from_context': {'type_id': 'type_id'},
            'row_matches_context_map': {'field': 'attrs', 'context_field': 'constraints'},
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
              'match_context_field': 'card_id',
              'set_on_context': {
                'field': 'card_id',
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
            'type_id': 'etype_ram',
            'constraints': {'ram_type': 'DDR4', 'module_capacity': '16'},
            'card_id': 'c_kingston',
          },
        },
        // две карточки, покрывающие требования (DDR4 16 ГБ)
        {
          'table_slug': 'part_cards',
          'row_id': 'c_kingston',
          'body': {
            'best_title': 'Kingston 16GB 3200',
            'part_number': 'KF432C16BB/16',
            'type_id': 'etype_ram',
            'attrs': {'ram_type': 'DDR4', 'module_capacity': '16'},
            'best_price': 3701.4,
          },
        },
        {
          'table_slug': 'part_cards',
          'row_id': 'c_corsair',
          'body': {
            'best_title': 'Corsair 16GB 3000',
            'part_number': 'CMK16GX4M2D3000C16',
            'type_id': 'etype_ram',
            'attrs': {'ram_type': 'DDR4', 'module_capacity': '16'},
            'best_price': 3831.6,
          },
        },
        // 32 ГБ — не покрывает constraints слота
        {
          'table_slug': 'part_cards',
          'row_id': 'c_32gb',
          'body': {
            'best_title': 'Kingston 32GB',
            'part_number': 'KF432C16BB/32',
            'type_id': 'etype_ram',
            'attrs': {'ram_type': 'DDR4', 'module_capacity': '32'},
            'best_price': 6900.0,
          },
        },
        // другой тип — не показывается
        {
          'table_slug': 'part_cards',
          'row_id': 'c_cpu',
          'body': {
            'best_title': 'Intel i5-12400F',
            'part_number': 'CM8071504553416',
            'type_id': 'etype_cpu',
            'attrs': {'socket': 'LGA1700', 'cores': '6'},
            'best_price': 11532.0,
          },
        },
      ],
    };

void main() {
  testWidgets(
    'пикер карточек фильтрует по типу и требованиям слота, выбор пишет card_id',
    (tester) async {
      tester.view.physicalSize = const Size(500, 900);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);

      final manifest = ModuleMetaManifest.fromJson(_manifest());
      final seeds = SeedDataController(manifest);
      final view = manifest.viewBySlug('part_cards_pick')!;

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

      // только карточки того же типа, покрывающие constraints слота
      expect(find.text('Kingston 16GB 3200'), findsOneWidget);
      expect(find.text('Corsair 16GB 3000'), findsOneWidget);
      expect(find.text('Kingston 32GB'), findsNothing);
      expect(find.text('Intel i5-12400F'), findsNothing);

      // выбираем Corsair (второй switch)
      final switches = tester.widgetList<Switch>(find.byType(Switch)).toList();
      expect(switches.length, 2);
      await tester.tap(find.byType(Switch).at(1));
      await tester.pumpAndSettle();

      final slot = seeds.bodyFor('slot_ram');
      expect(slot['card_id'], 'c_corsair');
    },
  );

  testWidgets('текущая закреплённая карточка подсвечена в пикере', (tester) async {
    tester.view.physicalSize = const Size(500, 900);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final manifest = ModuleMetaManifest.fromJson(_manifest());
    final seeds = SeedDataController(manifest);
    final view = manifest.viewBySlug('part_cards_pick')!;

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
    // текущий card_id слота = c_kingston — его switch включён
    expect(switches.where((s) => s.value).length, 1);
  });
}
