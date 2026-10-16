import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_icon_button.dart';
import 'package:prodavan/core/widgets/app_inline_add_field.dart';
import 'package:prodavan/features/meta/interpreters/collection_interpreter.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/features/meta/preview/seed_data_controller.dart';
import 'package:prodavan/l10n/app_localizations.dart';

Widget _ruApp(Widget home) {
  return MaterialApp(
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
}

Map<String, dynamic> _inlineCollectionView() => {
      'slug': 'items_list',
      'table_slug': 'items',
      'kind': 'collection',
      'ui_json': {
        'version': 1,
        'kind': 'collection',
        'title_field': 'name',
        'columns': [
          {'field': 'name', 'label': 'Имя'},
        ],
        'primary_action': {'kind': 'create_row', 'label': 'Добавить'},
        'row_tap': {'kind': 'open_form', 'view': 'items_form'},
        'inline_add': {
          'field': 'name',
          'title': 'Добавить MCP package',
        },
      },
    };

Map<String, dynamic> _collectionManifestJson() => {
      'syntax_version': 1,
      'tables': [
        {
          'slug': 'items',
          'label': 'Items',
          'storage_kind': 'json_document',
          'enabled': true,
        },
      ],
      'columns': [
        {
          'table_slug': 'items',
          'name': 'name',
          'label': 'Имя',
          'type': 'text',
          'required': true,
        },
      ],
      'views': [
        _inlineCollectionView(),
        {
          'slug': 'items_form',
          'table_slug': 'items',
          'kind': 'form',
          'ui_json': {
            'version': 1,
            'kind': 'form',
            'mode': 'edit',
            'fields': [
              {'column': 'name', 'widget': 'value'},
            ],
          },
        },
      ],
      'tabs': [],
    };

/// Budget-line → offers flow: row_tap with `context_field` must open the
/// linked row (id from the tapped row's body), not the tapped row itself.
Map<String, dynamic> _rowTapManifestJson() => {
      'syntax_version': 1,
      'tables': [
        {
          'slug': 'budget_lines',
          'label': 'Budget lines',
          'storage_kind': 'json_document',
          'enabled': true,
        },
        {
          'slug': 'offers',
          'label': 'Offers',
          'storage_kind': 'json_document',
          'enabled': true,
        },
      ],
      'columns': [
        {
          'table_slug': 'budget_lines',
          'name': 'title',
          'label': 'Название',
          'type': 'text',
        },
        {
          'table_slug': 'budget_lines',
          'name': 'line_id',
          'label': 'Line',
          'type': 'text',
        },
        {
          'table_slug': 'offers',
          'name': 'title',
          'label': 'Название',
          'type': 'text',
        },
      ],
      'views': [
        {
          'slug': 'budget_lines_list',
          'table_slug': 'budget_lines',
          'kind': 'collection',
          'ui_json': {
            'version': 1,
            'kind': 'collection',
            'title_field': 'title',
            'columns': [
              {'field': 'title', 'label': 'Название'},
            ],
            'row_tap': {
              'kind': 'open_view',
              'view': 'offers_for_line',
              'context_field': 'line_id',
            },
          },
        },
        {
          'slug': 'lines_no_ctx',
          'table_slug': 'budget_lines',
          'kind': 'collection',
          'ui_json': {
            'version': 1,
            'kind': 'collection',
            'title_field': 'title',
            'columns': [
              {'field': 'title', 'label': 'Название'},
            ],
            'row_tap': {'kind': 'open_view', 'view': 'offers_for_line'},
          },
        },
        {
          'slug': 'offers_for_line',
          'table_slug': 'offers',
          'kind': 'collection',
          'ui_json': {
            'version': 1,
            'kind': 'collection',
            'title_field': 'title',
            'columns': [
              {'field': 'title', 'label': 'Название'},
            ],
          },
        },
      ],
      'tabs': [],
      'seed_rows': {
        'items': [
          {
            'table_slug': 'budget_lines',
            'row_id': 'line1',
            'body': {'title': 'Линия 1', 'line_id': 'offer42'},
          },
          {
            'table_slug': 'budget_lines',
            'row_id': 'line2',
            'body': {'title': 'Линия 2'},
          },
          {
            'table_slug': 'offers',
            'row_id': 'offer42',
            'body': {'title': 'Оффер 42'},
          },
        ],
      },
    };

void main() {
  _wave7Group('WAVE7 collection features');
  _wave10Group('WAVE10 build slot pick');
  testWidgets('uses AppInlineAddField with meta title', (tester) async {
    final manifest = ModuleMetaManifest.fromJson(_collectionManifestJson());
    final seeds = SeedDataController(manifest);
    final view = manifest.viewBySlug('items_list')!;

    await tester.pumpWidget(
      _ruApp(
        CollectionViewInterpreter(
          manifest: manifest,
          view: view,
          seeds: seeds,
        ),
      ),
    );

    expect(find.byType(AppInlineAddField), findsOneWidget);
    expect(find.text('Добавить MCP package'), findsOneWidget);
  });

  testWidgets('suppresses toolbar create when inline_add present', (tester) async {
    tester.view.physicalSize = const Size(900, 700);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final manifest = ModuleMetaManifest.fromJson(_collectionManifestJson());
    final seeds = SeedDataController(manifest);
    final view = manifest.viewBySlug('items_list')!;

    await tester.pumpWidget(
      _ruApp(
        CollectionViewInterpreter(
          manifest: manifest,
          view: view,
          seeds: seeds,
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.byType(AppIconButton), findsNothing);
  });

  testWidgets('inline add creates a visible seed row', (tester) async {
    tester.view.physicalSize = const Size(900, 700);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final manifest = ModuleMetaManifest.fromJson(_collectionManifestJson());
    final seeds = SeedDataController(manifest);
    final view = manifest.viewBySlug('items_list')!;

    await tester.pumpWidget(
      _ruApp(
        CollectionViewInterpreter(
          manifest: manifest,
          view: view,
          seeds: seeds,
        ),
      ),
    );
    await tester.pumpAndSettle();

    await tester.tap(find.text('Добавить MCP package'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField), 'Alpha');
    await tester.testTextInput.receiveAction(TextInputAction.done);
    await tester.pumpAndSettle();

    expect(seeds.itemsForTable('items'), hasLength(1));
    expect(seeds.itemsForTable('items').first['body']['name'], 'Alpha');
    expect(find.text('Alpha'), findsWidgets);
  });

  group('row_tap context_field', () {
    Future<void> pumpList(
      WidgetTester tester,
      String viewSlug,
      List<(String, String?)> opened,
    ) async {
      tester.view.physicalSize = const Size(1200, 700);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);

      final manifest = ModuleMetaManifest.fromJson(_rowTapManifestJson());
      final seeds = SeedDataController(manifest);
      final view = manifest.viewBySlug(viewSlug)!;

      await tester.pumpWidget(
        _ruApp(
          CollectionViewInterpreter(
            manifest: manifest,
            view: view,
            seeds: seeds,
            onOpenForm: (viewSlug, {rowId}) => opened.add((viewSlug, rowId)),
          ),
        ),
      );
      await tester.pumpAndSettle();
    }

    testWidgets('opens the linked row id from the tapped row body',
        (tester) async {
      final opened = <(String, String?)>[];
      await pumpList(tester, 'budget_lines_list', opened);

      await tester.tap(find.text('Линия 1'));
      await tester.pumpAndSettle();

      expect(opened, [('offers_for_line', 'offer42')]);
    });

    testWidgets('falls back to the tapped row when the field is empty',
        (tester) async {
      final opened = <(String, String?)>[];
      await pumpList(tester, 'budget_lines_list', opened);

      await tester.tap(find.text('Линия 2'));
      await tester.pumpAndSettle();

      expect(opened, [('offers_for_line', 'line2')]);
    });

    testWidgets('without context_field passes the tapped row id',
        (tester) async {
      final opened = <(String, String?)>[];
      await pumpList(tester, 'lines_no_ctx', opened);

      await tester.tap(find.text('Линия 1'));
      await tester.pumpAndSettle();

      expect(opened, [('offers_for_line', 'line1')]);
    });
  });
}

/// WAVE7: сортировка / чекбокс-выбор / bool-ячейки / context_bind по полю.
Map<String, dynamic> _wave7ManifestJson() => {
      'syntax_version': 1,
      'tables': [
        {
          'slug': 'groups',
          'label': 'Groups',
          'storage_kind': 'json_document',
          'enabled': true,
        },
        {
          'slug': 'offers',
          'label': 'Offers',
          'storage_kind': 'json_document',
          'enabled': true,
        },
        {
          'slug': 'procurement',
          'label': 'Procurement',
          'storage_kind': 'json_document',
          'enabled': true,
        },
      ],
      'columns': [
        {
          'table_slug': 'groups',
          'name': 'title',
          'label': 'Товар',
          'type': 'text',
        },
        {
          'table_slug': 'groups',
          'name': 'rank',
          'label': 'Ранг',
          'type': 'number',
        },
        {
          'table_slug': 'groups',
          'name': 'face_price',
          'label': 'Цена',
          'type': 'number',
        },
        {
          'table_slug': 'offers',
          'name': 'title',
          'label': 'Товар',
          'type': 'text',
        },
        {
          'table_slug': 'offers',
          'name': 'seller',
          'label': 'Поставщик',
          'type': 'text',
        },
        {
          'table_slug': 'offers',
          'name': 'is_selected',
          'label': 'Выбран',
          'type': 'bool',
        },
        {
          'table_slug': 'procurement',
          'name': 'seller',
          'label': 'Поставщик',
          'type': 'text',
          'required': true,
        },
        {
          'table_slug': 'procurement',
          'name': 'margin_pct',
          'label': 'Маржа %',
          'type': 'number',
        },
        {
          'table_slug': 'procurement',
          'name': 'include_delivery',
          'label': 'Доставка',
          'type': 'bool',
        },
      ],
      'views': [
        {
          'slug': 'groups_list',
          'table_slug': 'groups',
          'kind': 'collection',
          'ui_json': {
            'version': 1,
            'kind': 'collection',
            'title_field': 'title',
            'columns': [
              {'field': 'title', 'label': 'Товар'},
              {'field': 'rank', 'label': 'Ранг'},
              {'field': 'face_price', 'label': 'Цена'},
            ],
            'sort': [
              {'field': 'rank', 'dir': 'asc'},
              {'field': 'face_price', 'dir': 'asc'},
            ],
          },
        },
        {
          'slug': 'offers_list',
          'table_slug': 'offers',
          'kind': 'collection',
          'ui_json': {
            'version': 1,
            'kind': 'collection',
            'title_field': 'title',
            'columns': [
              {'field': 'title', 'label': 'Товар'},
              {'field': 'seller', 'label': 'Поставщик'},
            ],
            'context_bind': {
              'seller': {'field': 'seller'},
            },
            'selection': {
              'kind': 'single',
              'field': 'is_selected',
              'action': 'select_offer_primary',
              'control': 'checkbox',
            },
          },
        },
        {
          'slug': 'procurement_list',
          'table_slug': 'procurement',
          'kind': 'collection',
          'ui_json': {
            'version': 1,
            'kind': 'collection',
            'title_field': 'seller',
            'columns': [
              {'field': 'seller', 'label': 'Поставщик'},
              {'field': 'margin_pct', 'label': 'Маржа %', 'editable': true},
              {'field': 'include_delivery', 'label': 'Доставка', 'editable': true},
            ],
          },
        },
      ],
      'tabs': [],
      'seed_rows': {
        'items': [
          {
            'table_slug': 'groups',
            'row_id': 'g1',
            'body': {'title': 'Аналог дороже', 'rank': 1, 'face_price': 200},
          },
          {
            'table_slug': 'groups',
            'row_id': 'g2',
            'body': {'title': 'Точный дешёвый', 'rank': 0, 'face_price': 150},
          },
          {
            'table_slug': 'groups',
            'row_id': 'g3',
            'body': {'title': 'Точный дорогой', 'rank': 0, 'face_price': 900},
          },
          {
            'table_slug': 'groups',
            'row_id': 'g4',
            'body': {'title': 'Без цены', 'rank': 2},
          },
          {
            'table_slug': 'offers',
            'row_id': 'o1',
            'body': {'title': 'Товар Иванова', 'seller': 'Иванов', 'is_selected': false},
          },
          {
            'table_slug': 'offers',
            'row_id': 'o2',
            'body': {'title': 'Товар Петрова', 'seller': 'Петров', 'is_selected': false},
          },
          {
            'table_slug': 'offers',
            'row_id': 'o3',
            'body': {
              'title': 'Ещё Иванов',
              'seller': 'Иванов',
              'is_selected': true,
            },
          },
          {
            'table_slug': 'procurement',
            'row_id': 'p1',
            'body': {
              'seller': 'Иванов',
              'margin_pct': 15,
              'include_delivery': false,
            },
          },
        ],
      },
    };

void _wave7Group(String description) {
  group(description, () {
    testWidgets('sort: rank asc then price asc, empty price last', (tester) async {
      tester.view.physicalSize = const Size(1200, 700);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);

      final manifest = ModuleMetaManifest.fromJson(_wave7ManifestJson());
      final seeds = SeedDataController(manifest);
      final view = manifest.viewBySlug('groups_list')!;

      await tester.pumpWidget(
        _ruApp(CollectionViewInterpreter(manifest: manifest, view: view, seeds: seeds)),
      );
      await tester.pumpAndSettle();

      final rows = tester
          .widgetList<AppEntityCollection>(
            find.byType(AppEntityCollection),
          )
          .first
          .rows;
      expect(
        rows.map((r) => r.title).toList(),
        ['Точный дешёвый', 'Точный дорогой', 'Аналог дороже', 'Без цены'],
      );
    });

    testWidgets('context_bind by field + checkbox selection', (tester) async {
      tester.view.physicalSize = const Size(1200, 700);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);

      final manifest = ModuleMetaManifest.fromJson(_wave7ManifestJson());
      final seeds = SeedDataController(manifest);
      final view = manifest.viewBySlug('offers_list')!;

      await tester.pumpWidget(
        _ruApp(
          CollectionViewInterpreter(
            manifest: manifest,
            view: view,
            seeds: seeds,
            contextRowId: 'p1', // procurement row: seller Иванов
          ),
        ),
      );
      await tester.pumpAndSettle();

      // контекстный бинд по полю: только офферы Иванова
      expect(find.text('Товар Иванова'), findsOneWidget);
      expect(find.text('Ещё Иванов'), findsOneWidget);
      expect(find.text('Товар Петрова'), findsNothing);

      // чекбокс вместо радио; выбранный ряд отмечен
      final checkboxes = tester.widgetList<Checkbox>(find.byType(Checkbox)).toList();
      expect(checkboxes.length, 2);
      expect(checkboxes.any((c) => c.value == true), isTrue);
    });

    testWidgets('bool editable cell toggles via patchField', (tester) async {
      tester.view.physicalSize = const Size(1200, 700);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);

      final manifest = ModuleMetaManifest.fromJson(_wave7ManifestJson());
      final seeds = SeedDataController(manifest);
      final view = manifest.viewBySlug('procurement_list')!;

      await tester.pumpWidget(
        _ruApp(CollectionViewInterpreter(manifest: manifest, view: view, seeds: seeds)),
      );
      await tester.pumpAndSettle();

      expect(find.byIcon(Icons.check_box_outline_blank), findsOneWidget);
      await tester.tap(find.byIcon(Icons.check_box_outline_blank));
      await tester.pumpAndSettle();

      final body = seeds.itemById('p1')!['body'] as Map<String, dynamic>;
      expect(body['include_delivery'], isTrue);
      expect(find.byIcon(Icons.check_box), findsOneWidget);
    });
  });
}

/// WAVE10: pick-вид кандидатов слота сборки — фильтр по contextRowId +
/// запись выбора в slots с пересчётом итога сборки из face_price группы.
Map<String, dynamic> _wave10ManifestJson() => {
      'syntax_version': 1,
      'tables': [
        {
          'slug': 'builds',
          'label': 'Builds',
          'storage_kind': 'json_document',
          'enabled': true,
        },
        {
          'slug': 'groups',
          'label': 'Groups',
          'storage_kind': 'json_document',
          'enabled': true,
        },
      ],
      'columns': [
        {'table_slug': 'builds', 'name': 'name', 'label': 'Название', 'type': 'text'},
        {'table_slug': 'builds', 'name': 'slots', 'label': 'Слоты', 'type': 'json'},
        {
          'table_slug': 'builds',
          'name': 'price_total',
          'label': 'Цена',
          'type': 'number',
        },
        {
          'table_slug': 'builds',
          'name': 'components_count',
          'label': 'Комплектующих',
          'type': 'number',
        },
        {'table_slug': 'groups', 'name': 'face_title', 'label': 'Товар', 'type': 'text'},
        {'table_slug': 'groups', 'name': 'build_id', 'label': 'Сборка', 'type': 'text'},
        {
          'table_slug': 'groups',
          'name': 'slot_type_id',
          'label': 'Слот',
          'type': 'text',
        },
        {
          'table_slug': 'groups',
          'name': 'face_price',
          'label': 'Цена',
          'type': 'number',
        },
        {'table_slug': 'groups', 'name': 'is_best', 'label': 'Лучший', 'type': 'bool'},
      ],
      'views': [
        {
          'slug': 'build_groups_pick',
          'table_slug': 'groups',
          'kind': 'collection',
          'ui_json': {
            'version': 1,
            'kind': 'collection',
            'title_field': 'face_title',
            'columns': [
              {'field': 'face_title', 'label': 'Товар'},
              {'field': 'face_price', 'label': 'Цена'},
            ],
            'row_filter_from_context': {
              'build_id': 'contextRowId',
              'slot_type_id': '_pick_type_id',
            },
            'selection': {
              'kind': 'single',
              'control': 'switch',
              'placement': 'trailing',
              'disable_row_tap': true,
              'match_map_field': 'slots',
              'match_map_key_from_context': '_slot_key',
              'set_on_context': {
                'map_field': 'slots',
                'map_key_from_context': '_slot_key',
                'value_from': 'row_id',
                'recompute_build_totals': true,
              },
            },
          },
        },
      ],
      'tabs': [],
      'seed_rows': {
        'items': [
          {
            'table_slug': 'builds',
            'row_id': 'b1',
            'body': {'name': 'ПК Intel', 'slots': <String, dynamic>{}},
          },
          {
            'table_slug': 'groups',
            'row_id': 'g_cheap',
            'body': {
              'face_title': 'CPU дешёвый',
              'build_id': 'b1',
              'slot_type_id': 'etype_cpu',
              'face_price': 100,
              'is_best': true,
            },
          },
          {
            'table_slug': 'groups',
            'row_id': 'g_dear',
            'body': {
              'face_title': 'CPU дорогой',
              'build_id': 'b1',
              'slot_type_id': 'etype_cpu',
              'face_price': 250,
            },
          },
          {
            'table_slug': 'groups',
            'row_id': 'g_other_slot',
            'body': {
              'face_title': 'RAM (другой слот)',
              'build_id': 'b1',
              'slot_type_id': 'etype_ram',
              'face_price': 50,
            },
          },
          {
            'table_slug': 'groups',
            'row_id': 'g_other_build',
            'body': {
              'face_title': 'CPU другой сборки',
              'build_id': 'b2',
              'slot_type_id': 'etype_cpu',
              'face_price': 999,
            },
          },
        ],
      },
    };

void _wave10Group(String description) {
  group(description, () {
    testWidgets('row_filter_from_context: contextRowId + pick-контекст слота',
        (tester) async {
      tester.view.physicalSize = const Size(500, 900);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);

      final manifest = ModuleMetaManifest.fromJson(_wave10ManifestJson());
      final seeds = SeedDataController(manifest);
      seeds.setPickContext(typeId: 'etype_cpu', slotKey: 'etype_cpu');
      final view = manifest.viewBySlug('build_groups_pick')!;

      await tester.pumpWidget(
        _ruApp(
          CollectionViewInterpreter(
            manifest: manifest,
            view: view,
            seeds: seeds,
            contextRowId: 'b1',
          ),
        ),
      );
      await tester.pumpAndSettle();

      // видны только кандидаты ЭТОЙ сборки и ЭТОГО слота
      expect(find.text('CPU дешёвый'), findsOneWidget);
      expect(find.text('CPU дорогой'), findsOneWidget);
      expect(find.text('RAM (другой слот)'), findsNothing);
      expect(find.text('CPU другой сборки'), findsNothing);
    });

    testWidgets('выбор кандидата пишет slots и пересчитывает итог из face_price',
        (tester) async {
      tester.view.physicalSize = const Size(500, 900);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);

      final manifest = ModuleMetaManifest.fromJson(_wave10ManifestJson());
      final seeds = SeedDataController(manifest);
      seeds.setPickContext(typeId: 'etype_cpu', slotKey: 'etype_cpu');
      final view = manifest.viewBySlug('build_groups_pick')!;

      await tester.pumpWidget(
        _ruApp(
          CollectionViewInterpreter(
            manifest: manifest,
            view: view,
            seeds: seeds,
            contextRowId: 'b1',
          ),
        ),
      );
      await tester.pumpAndSettle();

      // текущий выбор (is_best → slots пуст, поэтому switch выключены)
      final switches = tester.widgetList<Switch>(find.byType(Switch)).toList();
      expect(switches.length, 2);

      // выбираем дорогой вариант (второй ряд)
      await tester.tap(find.byType(Switch).at(1));
      await tester.pumpAndSettle();

      final build = seeds.bodyFor('b1');
      expect(build['slots'], {'etype_cpu': 'g_dear'});
      // итог считается из face_price группы, а не из легаси item→offer
      expect(build['price_total'], 250);
      expect(build['components_count'], 1);
    });
  });
}
