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
  {'column': 'contract_date', 'auto': 'today'},
  {'column': 'spec_number', 'auto': 'spec_seq'},
];
const _labels = {
  'supplier_name': 'Поставщик',
  'city': 'Город',
  'app_number': '№ приложения',
  'customer_name': 'Покупатель',
  'contract_number': '№ договора',
  'contract_date': 'Дата договора',
  'spec_number': '№ спецификации',
};
const _defaults = {
  'supplier_name': 'ООО "ИТ Взлёт"',
  'city': 'г. Москва',
  'app_number': '1',
  'customer_name': 'ООО «Ромашка»',
};

const _monthsGenRu = [
  'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
  'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
];

/// 1:1 с _todayRu панели (и fmt_date_ru рендера документов).
String _todayRu() {
  final now = DateTime.now();
  final day = now.day.toString().padLeft(2, '0');
  return '«$day» ${_monthsGenRu[now.month - 1]} ${now.year} г.';
}

String _textOf(WidgetTester tester, String label) => tester
    .widget<TextField>(find.widgetWithText(TextField, label))
    .controller!
    .text;

void main() {
  testWidgets('две карточки без общего заголовка; дефолты и авто-значения сразу',
      (tester) async {
    var created = 0;
    final upserts = <(String, Map<String, dynamic>)>[];
    await tester.pumpWidget(_wrap(DocumentFieldsPanel(
      companyTable: _companyTable,
      dealTable: _dealTable,
      companyTitle: const {'ru': 'Поставщик'},
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

    // заголовки карточек есть, общего «Реквизиты документов» нет
    expect(find.text('Поставщик (кабинет)'), findsNothing);
    expect(find.text('Реквизиты документов'), findsNothing);
    expect(find.text('Сделка'), findsOneWidget);
    // дефолты сразу в полях (строк ещё нет)
    expect(_textOf(tester, 'Поставщик'), 'ООО "ИТ Взлёт"');
    expect(_textOf(tester, 'Город'), 'г. Москва');
    // авто-номера из seq=0 → 0001; дата договора → сегодня
    expect(_textOf(tester, '№ договора'), '0001');
    expect(_textOf(tester, '№ спецификации'), '0001');
    expect(_textOf(tester, 'Дата договора'), _todayRu());

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
    expect(dealBody['contract_date'], _todayRu());
    expect(dealBody['spec_number'], '0001');
  });

  testWidgets('сохранённые значения перекрывают дефолты и авто-значения',
      (tester) async {
    var created = 0;
    final upserts = <(String, Map<String, dynamic>)>[];
    await tester.pumpWidget(_wrap(DocumentFieldsPanel(
      companyTable: _companyTable,
      dealTable: _dealTable,
      companyTitle: const {'ru': 'Поставщик'},
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
              'body': {
                'customer_name': 'ООО Сохранённый',
                'contract_date': '«01» января 2026 г.',
              },
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
    expect(_textOf(tester, 'Поставщик'), 'ООО Рог и Копыта');
    expect(_textOf(tester, 'Город'), 'г. Москва');
    expect(_textOf(tester, 'Покупатель'), 'ООО Сохранённый');
    // сохранённая дата не перезаписывается сегодняшней
    expect(_textOf(tester, 'Дата договора'), '«01» января 2026 г.');
    // seq 5/7 → следующие номера 0006/0008
    expect(_textOf(tester, '№ договора'), '0006');
    expect(_textOf(tester, '№ спецификации'), '0008');

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
    expect(dealBody['contract_date'], '«01» января 2026 г.');
    expect(dealBody['spec_number'], '0008');
  });
}
