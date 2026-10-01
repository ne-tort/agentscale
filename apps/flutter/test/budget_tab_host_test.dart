import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/features/meta/interpreters/collection_interpreter.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/features/meta/runtime/cabinet_data_controller.dart';
import 'package:prodavan/features/meta/runtime/runtime_data_adapter.dart';
import 'package:prodavan/l10n/app_localizations.dart';

Widget _host(Widget child) => MaterialApp(
      localizationsDelegates: AppLocalizations.localizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      locale: const Locale('ru'),
      home: Scaffold(body: Center(child: child)),
    );

Map<String, dynamic> _budgetView() => {
      'slug': 'budget_lines_list',
      'kind': 'collection',
      'table_slug': 'budget_lines',
      'label': 'Бюджетирование',
      'ui_json': {
        'scaffold': {
          'actions': [
            {
              'kind': 'invoke_action',
              'action': 'budget_sync_lines',
              'label': {'ru': 'Синхронизировать'},
            }
          ],
        },
        'summary': {'kind': 'budget_totals'},
        'columns': [
          {'field': 'title', 'label': {'ru': 'Товар'}},
          {'field': 'qty', 'label': {'ru': 'Кол-во'}},
        ],
      },
    };

ModuleMetaManifest _manifest() => ModuleMetaManifest.fromJson({
      'views': [_budgetView()],
    });

CabinetDataController _controller(ModuleMetaManifest manifest) =>
    CabinetDataController(
      api: ProdavanApi(baseUrl: 'http://localhost:1', bearerToken: 't'),
      cabinetId: 'c1',
      moduleId: 'mod_equipment',
      manifest: manifest,
    );

void main() {
  testWidgets('budget view renders summary table with adapter seeds (tab-host path)', (tester) async {
    final manifest = _manifest();
    final adapter = RuntimeDataAdapter(_controller(manifest));
    await tester.pumpWidget(_host(CollectionViewInterpreter(
      manifest: manifest,
      view: _budgetView(),
      seeds: adapter,
    )));
    await tester.pumpAndSettle();

    // Summary table header (the "second table" for totals).
    expect(find.text('Показатель'), findsOneWidget);
    expect(find.text('Значение'), findsOneWidget);
    expect(find.text('Закупка с НДС'), findsOneWidget);
    expect(find.text('Маржа %'), findsOneWidget);
  });

  testWidgets('budget summary renders with empty rows (always visible)', (tester) async {
    final manifest = _manifest();
    final adapter = RuntimeDataAdapter(_controller(manifest));
    await tester.pumpWidget(_host(CollectionViewInterpreter(
      manifest: manifest,
      view: _budgetView(),
      seeds: adapter,
    )));
    await tester.pumpAndSettle();

    expect(find.text('Позиций'), findsOneWidget);
  });
}
