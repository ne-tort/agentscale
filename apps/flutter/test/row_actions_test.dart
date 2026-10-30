import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/features/meta/interpreters/collection_interpreter.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/features/meta/preview/seed_data_controller.dart';
import 'package:prodavan/l10n/app_localizations.dart';

Map<String, dynamic> _manifestJson() => {
      'syntax_version': 1,
      'tables': [
        {
          'slug': 'tenders',
          'label': 'Тендеры',
          'storage_kind': 'json_document',
          'enabled': true,
        },
      ],
      'columns': [
        {
          'table_slug': 'tenders',
          'name': 'title',
          'label': 'Название',
          'type': 'text',
        },
        {
          'table_slug': 'tenders',
          'name': 'work_status',
          'label': 'Статус',
          'type': 'text',
        },
      ],
      'views': [
        {
          'slug': 'tenders_list',
          'table_slug': 'tenders',
          'kind': 'collection',
          'ui_json': {
            'version': 1,
            'kind': 'collection',
            'title_field': 'title',
            'columns': [
              {'field': 'work_status', 'label': 'Статус'},
            ],
            'row_actions': [
              {
                'action': 'tender_take_to_work',
                'icon': 'playlist_add_check',
                'label': 'Взять в работу',
                'when': {'field': 'work_status', 'eq': 'new'},
              },
            ],
          },
        },
      ],
      'seed_rows': {
        'items': [
          {
            'table_slug': 'tenders',
            'row_id': 'tnd_1',
            'body': {'title': 'Серверы', 'work_status': 'new'},
          },
          {
            'table_slug': 'tenders',
            'row_id': 'tnd_2',
            'body': {'title': 'Ноутбуки', 'work_status': 'in_progress'},
          },
        ],
      },
    };

void main() {
  testWidgets('row_actions renders one icon only for matching rows', (tester) async {
    final manifest = ModuleMetaManifest.fromJson(_manifestJson());
    final seeds = SeedDataController(manifest);
    final view = manifest.viewBySlug('tenders_list')!;
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light,
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
            manifest: manifest,
            view: view,
            seeds: seeds,
          ),
        ),
      ),
    );
    await tester.pump();
    expect(find.byTooltip('Взять в работу'), findsOneWidget);
    expect(find.byIcon(Icons.playlist_add_check_rounded), findsOneWidget);
  });
}
