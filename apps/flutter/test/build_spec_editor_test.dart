import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/features/meta/preview/seed_data_controller.dart';
import 'package:prodavan/features/meta/widgets/build_spec_editor.dart';
import 'package:prodavan/features/meta/widgets/ready_build_slots_table.dart';
import 'package:prodavan/features/meta/widgets/target_price_field.dart';
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

ModuleMetaManifest _manifest() => ModuleMetaManifest.fromJson({
      'syntax_version': 1,
      'tables': [
        {'slug': 'ready_builds', 'label': 'Сборки', 'storage_kind': 'json_document', 'enabled': true},
        {'slug': 'ready_build_slots', 'label': 'Слоты', 'storage_kind': 'json_document', 'enabled': true},
        {'slug': 'equipment_types', 'label': 'Типы', 'storage_kind': 'json_document', 'enabled': true},
      ],
      'columns': const [],
      'views': const [],
      'seed_rows': [
        {
          'table_slug': 'equipment_types',
          'row_id': 'etype_cpu',
          'body': {
            'name': 'Процессор',
            'fields_json': [
              {
                'key': 'socket',
                'label': {'ru': 'Сокет'},
                'kind': 'enum',
                'required': true,
                'compat_domain': 'socket',
                'values': ['AM4', 'LGA1700'],
              },
            ],
          },
        },
        {
          'table_slug': 'equipment_types',
          'row_id': 'etype_ram',
          'body': {
            'name': 'Оперативная память',
            'fields_json': [
              {
                'key': 'ram_type',
                'label': {'ru': 'Тип ОЗУ'},
                'kind': 'enum',
                'required': true,
                'compat_domain': 'ram_type',
                'values': ['DDR4', 'DDR5'],
              },
            ],
          },
        },
        {
          'table_slug': 'equipment_types',
          'row_id': 'etype_storage',
          'body': {
            'name': 'Накопитель',
            'fields_json': [
              {
                'key': 'capacity',
                'label': {'ru': 'Объём'},
                'kind': 'groups',
                'required': true,
                'groups': [
                  {'value': '256', 'label': '256 ГБ'},
                  {'value': '512', 'label': '512 ГБ'},
                ],
              },
            ],
          },
        },
        {'table_slug': 'ready_builds', 'row_id': 'rb1', 'body': {'name': 'ПК', 'build_kind': 'pc'}},
        {
          'table_slug': 'ready_build_slots',
          'row_id': 'sl_cpu',
          'body': {
            'build_id': 'rb1',
            'type_id': 'etype_cpu',
            'type_name': 'Процессор',
            'constraints': {'socket': 'LGA1700'},
            'qty': 1,
            'resolved_title': 'Intel i5-12400F',
            'resolved_price': 11532.0,
            'line_total': 11532.0,
            'alternatives_count': 1,
          },
        },
        {
          'table_slug': 'ready_build_slots',
          'row_id': 'sl_ram',
          'body': {
            'build_id': 'rb1',
            'type_id': 'etype_ram',
            'type_name': 'Оперативная память',
            'constraints': {'ram_type': 'DDR4', 'ram_total': '16'},
            'qty': 2,
            'resolved_title': 'Kingston 16GB',
            'resolved_price': 3701.4,
            'line_total': 7402.8,
            'alternatives_count': 0,
          },
        },
      ],
    });

void main() {
  group('parseTargetPriceRange', () {
    test('диапазон «100-10000»', () {
      expect(parseTargetPriceRange('100-10000'), (100, 10000));
    });

    test('одно число — минимум', () {
      expect(parseTargetPriceRange('100'), (100, null));
    });

    test('«0-10000» — не дороже 10000', () {
      expect(parseTargetPriceRange('0-10000'), (0, 10000));
    });

    test('пустая строка — очистка', () {
      expect(parseTargetPriceRange(''), (null, null));
      expect(parseTargetPriceRange('   '), (null, null));
    });

    test('запятая и точка как разделитель дробной части', () {
      expect(parseTargetPriceRange('100,5-10000.75'), (100.5, 10000.75));
    });

    test('лишние знаки после запятой отсекаются', () {
      expect(parseTargetPriceRange('100,999'), (100.99, null));
      expect(parseTargetPriceRange('0.299-10'), (0.29, 10));
    });

    test('пробелы и тире нормализуются', () {
      expect(parseTargetPriceRange('10 000 – 20 000'), (10000, 20000));
    });

    test('мусор — null', () {
      expect(parseTargetPriceRange('abc'), isNull);
      expect(parseTargetPriceRange('1-2-3'), isNull);
      expect(parseTargetPriceRange('-'), isNull);
    });
  });

  testWidgets('TargetPriceRangeField: одно поле, коридор зелёным', (tester) async {
    num? savedMin;
    num? savedMax;
    await tester.pumpWidget(
      _ruApp(
        TargetPriceRangeField(
          label: 'Целевая цена',
          valueMin: 30000,
          valueMax: 45000,
          priceMin: 37414,
          priceMax: 49299,
          readOnly: false,
          onChanged: (min, max) {
            savedMin = min;
            savedMax = max;
          },
        ),
      ),
    );
    // коридор возможных комбинаций — зелёным справа
    expect(find.text('37414-49299 ₽'), findsOneWidget);
    // текущее значение одной строкой
    expect(find.text('30000-45000'), findsOneWidget);

    // тап → инлайн-редактирование → ввод диапазона
    await tester.tap(find.text('30000-45000'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField), '31000.5-48000');
    await tester.testTextInput.receiveAction(TextInputAction.done);
    await tester.pumpAndSettle();
    expect(savedMin, 31000.5);
    expect(savedMax, 48000);
  });

  testWidgets('BuildSpecEditor: девять полей, выбор пишет constraints слота',
      (tester) async {
    tester.view.physicalSize = const Size(700, 1600);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final manifest = _manifest();
    final seeds = SeedDataController(manifest);

    await tester.pumpWidget(
      _ruApp(
        BuildSpecEditor(
          seeds: seeds,
          buildId: 'rb1',
          slotsTable: 'ready_build_slots',
          typesTable: 'equipment_types',
          readOnly: false,
          buildKind: 'pc',
        ),
      ),
    );
    await tester.pumpAndSettle();

    // канонический контракт: сокет, DDR, RAM, SSD, HDD, БП, GPU, формфактор, водянка
    expect(find.text('Сокет'), findsOneWidget);
    expect(find.text('DDR, версия'), findsOneWidget);
    expect(find.text('RAM, гб'), findsOneWidget);
    expect(find.text('SSD, гб'), findsOneWidget);
    expect(find.text('HDD, гб'), findsOneWidget);
    expect(find.text('Питание, вт'), findsOneWidget);
    expect(find.text('GPU, гб'), findsOneWidget);
    expect(find.text('Формфактор'), findsOneWidget);
    expect(find.text('Водянка, да-нет'), findsOneWidget);

    // сокет слота показан текущим значением
    expect(find.text('LGA1700'), findsWidgets);

    // меняем DDR-версию на DDR5
    await tester.tap(find.text('DDR4').first);
    await tester.pumpAndSettle();
    await tester.tap(find.text('DDR5').last);
    await tester.pumpAndSettle();

    final ramSlot = seeds.bodyFor('sl_ram');
    expect((ramSlot['constraints'] as Map)['ram_type'], 'DDR5');
  });

  testWidgets('ReadyBuildSlotsTable: строки слотов, qty редактируется для ОЗУ',
      (tester) async {
    tester.view.physicalSize = const Size(900, 600);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final manifest = _manifest();
    final seeds = SeedDataController(manifest);

    await tester.pumpWidget(
      _ruApp(
        ReadyBuildSlotsTable(
          seeds: seeds,
          buildId: 'rb1',
          slotsTable: 'ready_build_slots',
          readOnly: false,
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Процессор'), findsOneWidget);
    expect(find.text('Оперативная память'), findsOneWidget);
    expect(find.text('Intel i5-12400F'), findsOneWidget);
    // количество редактируется: у ОЗУ текстовое поле, у процессора — статик «×1»
    expect(find.byType(TextField), findsOneWidget);
    expect(find.text('×1'), findsOneWidget);
    // сумма слота ОЗУ = 2 × 3701.4
    expect(find.text('7402.8 ₽'), findsOneWidget);
  });
}
