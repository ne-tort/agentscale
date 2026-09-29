import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/theme/app_theme.dart';
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
