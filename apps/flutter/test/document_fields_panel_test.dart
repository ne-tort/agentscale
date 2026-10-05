import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/l10n/app_localizations.dart';

import 'package:prodavan/features/meta/widgets/document_fields_panel.dart';
import 'package:prodavan/l10n/app_localizations_ru.dart';

void main() {
  testWidgets('панель реквизитов: рендер полей и автосохранение', (tester) async {
    final l10n = AppLocalizationsRu();
    var created = 0;
    final upserted = <Map<String, dynamic>>[];
    await tester.pumpWidget(
      MaterialApp(
        locale: const Locale('ru'),
        supportedLocales: AppLocalizations.supportedLocales,
        localizationsDelegates: const [
          AppLocalizations.delegate,
          GlobalMaterialLocalizations.delegate,
          GlobalWidgetsLocalizations.delegate,
          GlobalCupertinoLocalizations.delegate,
        ],
        home: Scaffold(
          body: SizedBox(
            height: 600,
            child: DocumentFieldsPanel(
              tableSlug: 'document_fields',
              title: {'ru': 'Реквизиты документов'},
              fields: const ['supplier_name', 'contract_number'],
              labels: const {
                'supplier_name': 'Поставщик',
                'contract_number': '№ договора',
              },
              itemsForTable: (_) => const [],
              itemById: (_) => null,
              createRow: (_) async {
                created += 1;
                return 'row_new';
              },
              upsertBody: (rowId, body) async {
                upserted.add(body);
              },
            ),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Реквизиты документов'), findsOneWidget);
    expect(find.text('Поставщик'), findsOneWidget);

    await tester.enterText(find.byType(TextField).first, 'ООО Тест');
    await tester.enterText(find.byType(TextField).last, '2026/77');
    // debounce 600ms → save
    await tester.pump(const Duration(milliseconds: 700));
    await tester.pumpAndSettle();
    expect(created, 1);
    expect(upserted, isNotEmpty);
    expect(upserted.last['supplier_name'], 'ООО Тест');
    expect(upserted.last['contract_number'], '2026/77');
    // l10n используется для заголовка панели
    expect(l10n.budgetBenefitSingle, 'Единственный');
  });
}
