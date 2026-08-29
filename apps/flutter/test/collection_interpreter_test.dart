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
}
