import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/l10n/app_localizations.dart';

import 'package:prodavan/features/meta/widgets/document_fields_panel.dart';

Widget _wrap(Widget child) => MaterialApp(
      locale: const Locale('ru'),
      supportedLocales: AppLocalizations.supportedLocales,
      localizationsDelegates: const [
        AppLocalizations.delegate,
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      home: Scaffold(
        body: SingleChildScrollView(child: child),
      ),
    );

const _companyTable = 'document_company_fields';
const _dealTable = 'document_fields';
const _companyFields = ['supplier_name', 'city', 'app_number'];
const _dealFields = [
  {'column': 'customer_name'},
  {'column': 'contract_number', 'auto': 'contract_seq'},
  {'column': 'spec_number', 'auto': 'spec_seq'},
];
const _labels = {
  'supplier_name': 'Поставщик',
  'city': 'Город',
  'app_number': '№ приложения',
  'customer_name': 'Покупатель',
  'contract_number': '№ договора',
  'spec_number': '№ спецификации',
};
const _defaults = {
  'supplier_name': 'ООО "ИТ Взлёт"',
  'city': 'г. Москва',
  'app_number': '1',
  'customer_name': 'ООО «Ромашка»',
};

void main() {
  testWidgets('дефолты из шаблона подставляются сразу, номера генерятся 0001',
      (tester) async {
    var created = 0;
    final upserts = <(String, Map<String, dynamic>)>[];
    await tester.pumpWidget(_wrap(DocumentFieldsPanel(
      companyTable: _companyTable,
      dealTable: _dealTable,
      title: const {'ru': 'Реквизиты документов'},
      companyTitle: const {'ru': 'Поставщик (кабинет)'},
      dealTitle: const {'ru': 'Сделка'},
      companyFields: _companyFields,
      dealFields: _dealFields.map((m) => Map<String, dynamic>.from(m)).toList(),
      labels: _labels,
      defaults: _defaults,
      itemsForTable: (_) => const [],
      createRow: (_) async {
        created += 1;
        return 'row_$created';
      },
      upsertBody: (rowId, body) async {
        upserts.add((rowId, body));
      },
    )));
    await tester.pumpAndSettle();

    expect(find.text('Реквизиты документов'), findsOneWidget);
    expect(find.text('Поставщик (кабинет)'), findsOneWidget);
    // дефолты сразу в полях (строк ещё нет)
    expect(
      tester.widget<TextField>(find.widgetWithText(TextField, 'Поставщик')).controller?.text,
      'ООО "ИТ Взлёт"',
    );
    expect(
      tester.widget<TextField>(find.widgetWithText(TextField, 'Город')).controller?.text,
      'г. Москва',
    );
    // авто-номера из seq=0 → 0001
    expect(
      tester.widget<TextField>(find.widgetWithText(TextField, '№ договора')).controller?.text,
      '0001',
    );
    expect(
      tester.widget<TextField>(find.widgetWithText(TextField, '№ спецификации')).controller?.text,
      '0001',
    );

    await tester.enterText(find.widgetWithText(TextField, 'Покупатель'), 'ООО Клиент');
    await tester.pump(const Duration(milliseconds: 700)); // debounce → save
    await tester.pumpAndSettle();

    expect(created, 2); // компания + сделка
    expect(upserts.length, 2);
    final (companyId, companyBody) = upserts[0];
    expect(companyId, 'row_1');
    expect(companyBody['supplier_name'], 'ООО "ИТ Взлёт"');
    expect(companyBody['city'], 'г. Москва');
    // выданные авто-номера поднимают seq-счётчики компании
    expect(companyBody['contract_seq'], 1);
    expect(companyBody['spec_seq'], 1);
    final (dealId, dealBody) = upserts[1];
    expect(dealId, 'row_2');
    expect(dealBody['customer_name'], 'ООО Клиент');
    expect(dealBody['contract_number'], '0001');
    expect(dealBody['spec_number'], '0001');
  });

  testWidgets('сохранённые значения перекрывают дефолты, номера продолжаются из seq',
      (tester) async {
    var created = 0;
    final upserts = <(String, Map<String, dynamic>)>[];
    await tester.pumpWidget(_wrap(DocumentFieldsPanel(
      companyTable: _companyTable,
      dealTable: _dealTable,
      title: const {'ru': 'Реквизиты документов'},
      companyTitle: const {'ru': 'Поставщик (кабинет)'},
      dealTitle: const {'ru': 'Сделка'},
      companyFields: _companyFields,
      dealFields: _dealFields.map((m) => Map<String, dynamic>.from(m)).toList(),
      labels: _labels,
      defaults: _defaults,
      itemsForTable: (slug) => switch (slug) {
        _companyTable => [
            {
              'row_id': 'c1',
              'body': {
                'supplier_name': 'ООО Рог и Копыта',
                'contract_seq': 5,
                'spec_seq': 7,
              },
            }
          ],
        _dealTable => [
            {
              'row_id': 'd1',
              'body': {'customer_name': 'ООО Сохранённый'},
            }
          ],
        _ => const [],
      },
      createRow: (_) async {
        created += 1;
        return 'row_new';
      },
      upsertBody: (rowId, body) async {
        upserts.add((rowId, body));
      },
    )));
    await tester.pumpAndSettle();

    // тело строки важнее дефолта; отсутствующее поле берёт дефолт
    expect(
      tester.widget<TextField>(find.widgetWithText(TextField, 'Поставщик')).controller?.text,
      'ООО Рог и Копыта',
    );
    expect(
      tester.widget<TextField>(find.widgetWithText(TextField, 'Город')).controller?.text,
      'г. Москва',
    );
    expect(
      tester.widget<TextField>(find.widgetWithText(TextField, 'Покупатель')).controller?.text,
      'ООО Сохранённый',
    );
    // seq 5/7 → следующие номера 0006/0008
    expect(
      tester.widget<TextField>(find.widgetWithText(TextField, '№ договора')).controller?.text,
      '0006',
    );
    expect(
      tester.widget<TextField>(find.widgetWithText(TextField, '№ спецификации')).controller?.text,
      '0008',
    );

    await tester.enterText(find.widgetWithText(TextField, 'Город'), 'г. Тверь');
    await tester.pump(const Duration(milliseconds: 700));
    await tester.pumpAndSettle();

    expect(created, 0); // строки уже есть — обновляем их
    expect(upserts.length, 2);
    final (companyId, companyBody) = upserts[0];
    expect(companyId, 'c1');
    expect(companyBody['city'], 'г. Тверь');
    expect(companyBody['supplier_name'], 'ООО Рог и Копыта');
    expect(companyBody['contract_seq'], 6);
    expect(companyBody['spec_seq'], 8);
    final (dealId, dealBody) = upserts[1];
    expect(dealId, 'd1');
    expect(dealBody['contract_number'], '0006');
    expect(dealBody['spec_number'], '0008');
  });
}
