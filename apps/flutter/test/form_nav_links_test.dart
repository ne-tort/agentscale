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

/// Минимальный манифест: таблица «build_groups», одна строка, detail-страница
/// с полем «Название» и ссылкой «Компоненты пула».
Map<String, dynamic> _manifest() => {
      'syntax_version': 1,
      'tables': [
        {
          'slug': 'build_groups',
          'label': 'Группы сборок',
          'storage_kind': 'json_document',
          'enabled': true,
        },
        {
          'slug': 'build_group_items',
          'label': 'Компоненты пула',
          'storage_kind': 'json_document',
          'enabled': true,
        },
      ],
      'columns': [
        {
          'table_slug': 'build_groups',
          'name': 'name',
          'label': 'Название',
          'type': 'text',
        },
      ],
      'views': [
        {
          'slug': 'build_group_settings',
          'table_slug': 'build_groups',
          'kind': 'detail',
          'ui_json': {
            'version': 1,
            'kind': 'detail',
            'mode': 'edit',
            'fields': [
              {'column': 'name', 'widget': 'value', 'icon': 'label_outline'},
            ],
            'links': [
              {
                'label': {'ru': 'Пул компонентов', 'en': 'Component pool'},
                'icon': 'inventory_2',
                'open_view': 'build_group_items_list',
              },
            ],
          },
        },
        {
          'slug': 'build_group_items_list',
          'table_slug': 'build_group_items',
          'kind': 'collection',
          'ui_json': {'version': 1, 'kind': 'collection', 'title_field': 'class_label'},
        },
      ],
      'seed_rows': [
        {
          'table_slug': 'build_groups',
          'row_id': 'grp_1',
          'body': {'name': 'AM4 · DDR4 · бюджетный'},
        },
      ],
    };

void main() {
  testWidgets('detail-страница показывает поля-ссылки и открывает целевой вид', (
    tester,
  ) async {
    final manifest = ModuleMetaManifest.fromJson(_manifest());
    final seeds = SeedDataController(manifest);
    final view = manifest.viewBySlug('build_group_settings')!;

    String? openedView;
    String? openedRowId;
    await tester.pumpWidget(
      _ruApp(
        FormViewInterpreter(
          manifest: manifest,
          view: view,
          seeds: seeds,
          rowId: 'grp_1',
          onOpenView: (slug, {rowId}) {
            openedView = slug;
            openedRowId = rowId;
          },
        ),
      ),
    );
    await tester.pumpAndSettle();

    // и поле, и ссылка на месте
    expect(find.text('Название'), findsOneWidget);
    expect(find.text('Пул компонентов'), findsOneWidget);

    await tester.tap(find.text('Пул компонентов'));
    await tester.pumpAndSettle();

    // открывает связанный список с контекстом текущей строки
    expect(openedView, 'build_group_items_list');
    expect(openedRowId, 'grp_1');
  });

  testWidgets('detail-страница без links рендерится как раньше', (tester) async {
    final manifest = ModuleMetaManifest.fromJson(_manifest());
    final seeds = SeedDataController(manifest);
    final view = manifest.viewBySlug('build_group_settings')!;
    // links убраны из копии — ровно старое поведение
    final fields =
        (view['ui_json'] as Map<String, dynamic>)['fields'] as List<dynamic>;
    final viewNoLinks = Map<String, dynamic>.from(view);
    viewNoLinks['ui_json'] = {
      'version': 1,
      'kind': 'detail',
      'mode': 'edit',
      'fields': fields,
    };

    await tester.pumpWidget(
      _ruApp(
        FormViewInterpreter(
          manifest: manifest,
          view: viewNoLinks,
          seeds: seeds,
          rowId: 'grp_1',
          onOpenView: (_, {rowId}) {},
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Название'), findsOneWidget);
    expect(find.text('Пул компонентов'), findsNothing);
  });
}
