import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/features/meta/interpreters/form_interpreter.dart';
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

/// WAVE12: detail-формы — constraint_attrs (значения из групп типа) и
/// links с row_id_from_field (переход по ссылке из поля, а не по открытой
/// строке).
Map<String, dynamic> _manifest() => {
      'syntax_version': 1,
      'tables': [
        {'slug': 'part_cards', 'label': 'Карточки', 'storage_kind': 'json_document', 'enabled': true},
        {'slug': 'equipment_types', 'label': 'Типы', 'storage_kind': 'json_document', 'enabled': true},
        {'slug': 'equipment_builds', 'label': 'Сборки', 'storage_kind': 'json_document', 'enabled': true},
        {'slug': 'ready_builds', 'label': 'Готовые', 'storage_kind': 'json_document', 'enabled': true},
      ],
      'columns': [
        {'table_slug': 'part_cards', 'name': 'type_id', 'label': 'Тип', 'type': 'text'},
        {'table_slug': 'part_cards', 'name': 'attrs', 'label': 'Характеристики', 'type': 'json'},
        {'table_slug': 'equipment_builds', 'name': 'name', 'label': 'Название', 'type': 'text'},
        {'table_slug': 'equipment_builds', 'name': 'ready_build_id', 'label': 'Готовая', 'type': 'text'},
        {'table_slug': 'ready_builds', 'name': 'name', 'label': 'Название', 'type': 'text'},
      ],
      'views': [
        {
          'slug': 'part_card_settings',
          'table_slug': 'part_cards',
          'kind': 'detail',
          'ui_json': {
            'version': 1,
            'kind': 'detail',
            'mode': 'edit',
            'fields': [
              {
                'column': 'attrs',
                'widget': 'constraint_attrs',
                'type_id_field': 'type_id',
                'types_table': 'equipment_types',
              },
            ],
          },
        },
        {
          'slug': 'equipment_build_settings',
          'table_slug': 'equipment_builds',
          'kind': 'detail',
          'ui_json': {
            'version': 1,
            'kind': 'detail',
            'mode': 'edit',
            'fields': [
              {'column': 'name', 'widget': 'value'},
            ],
            'links': [
              {
                'label': 'Открыть готовую сборку',
                'open_view': 'ready_build_settings',
                'row_id_from_field': 'ready_build_id',
              },
            ],
          },
        },
        {
          'slug': 'ready_build_settings',
          'table_slug': 'ready_builds',
          'kind': 'detail',
          'ui_json': {
            'version': 1,
            'kind': 'detail',
            'mode': 'edit',
            'fields': [
              {'column': 'name', 'widget': 'value'},
            ],
          },
        },
      ],
      'seed_rows': [
        {
          'table_slug': 'equipment_types',
          'row_id': 'etype_storage',
          'body': {
            'name': 'Накопитель',
            'fields_json': [
              {
                'key': 'drive_type',
                'label': {'ru': 'Тип'},
                'kind': 'enum',
                'required': true,
                'values': ['SSD', 'HDD'],
              },
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
              {'key': 'note', 'label': {'ru': 'Заметка'}, 'kind': 'text'},
            ],
          },
        },
        {
          'table_slug': 'part_cards',
          'row_id': 'card_1',
          'body': {
            'type_id': 'etype_storage',
            'attrs': {'drive_type': 'SSD', 'capacity': '512'},
          },
        },
        {
          'table_slug': 'equipment_builds',
          'row_id': 'link_1',
          'body': {'name': 'Сборка в чате', 'ready_build_id': 'rb_9'},
        },
        {
          'table_slug': 'ready_builds',
          'row_id': 'rb_9',
          'body': {'name': 'Готовая девять'},
        },
      ],
    };

void main() {
  testWidgets('constraint_attrs: выбор значения из групп типа пишется в attrs',
      (tester) async {
    tester.view.physicalSize = const Size(700, 1200);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final manifest = ModuleMetaManifest.fromJson(_manifest());
    final seeds = SeedDataController(manifest);
    final view = manifest.viewBySlug('part_card_settings')!;

    await tester.pumpWidget(
      _ruApp(
        FormViewInterpreter(
          manifest: manifest,
          view: view,
          seeds: seeds,
          rowId: 'card_1',
        ),
      ),
    );
    await tester.pumpAndSettle();

    // группы-поля показывают выбор с текущим значением, required помечены «*»
    expect(find.text('Тип *'), findsOneWidget);
    expect(find.text('Объём *'), findsOneWidget);
    expect(find.text('SSD'), findsOneWidget);
    expect(find.text('512 ГБ'), findsOneWidget);
    // текстовое поле тоже есть
    expect(find.text('Заметка'), findsOneWidget);

    // меняем объём: тап по полю → выбор 256 ГБ
    await tester.tap(find.text('512 ГБ'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('256 ГБ').last);
    await tester.pumpAndSettle();

    final body = seeds.bodyFor('card_1');
    final attrs = body['attrs'] as Map;
    expect(attrs['capacity'], '256');
    expect(attrs['drive_type'], 'SSD');
  });

  testWidgets('links с row_id_from_field ведут на строку из поля, не на себя',
      (tester) async {
    tester.view.physicalSize = const Size(700, 1200);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final manifest = ModuleMetaManifest.fromJson(_manifest());
    final seeds = SeedDataController(manifest);
    final view = manifest.viewBySlug('equipment_build_settings')!;

    String? openedView;
    String? openedRowId;
    await tester.pumpWidget(
      _ruApp(
        FormViewInterpreter(
          manifest: manifest,
          view: view,
          seeds: seeds,
          rowId: 'link_1',
          onOpenView: (slug, {rowId}) {
            openedView = slug;
            openedRowId = rowId;
          },
        ),
      ),
    );
    await tester.pumpAndSettle();

    await tester.tap(find.text('Открыть готовую сборку'));
    expect(openedView, 'ready_build_settings');
    // переход по ready_build_id из тела ссылки, а не по row_id самой ссылки
    expect(openedRowId, 'rb_9');
  });
}
