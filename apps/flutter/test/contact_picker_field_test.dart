import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/features/meta/widgets/contact_picker_field.dart';
import 'package:prodavan/l10n/app_localizations.dart';

void main() {
  final rows = <Map<String, dynamic>>[
    {
      'row_id': 'c1',
      'body': {'name': 'Клиент', 'email': 'client@example.com'},
    },
    {
      'row_id': 'c2',
      'body': {'name': 'Снабжение', 'email': 'supply@corp.example'},
    },
  ];

  Future<void> pumpField(
    WidgetTester tester, {
    String? value,
    bool readOnly = false,
    required void Function(String?) onChanged,
  }) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light,
        locale: const Locale('ru'),
        localizationsDelegates: const [
          AppLocalizations.delegate,
          GlobalMaterialLocalizations.delegate,
          GlobalWidgetsLocalizations.delegate,
          GlobalCupertinoLocalizations.delegate,
        ],
        supportedLocales: AppLocalizations.supportedLocales,
        home: Scaffold(
          body: ContactPickerField(
            label: 'Получатель',
            rows: rows,
            value: value,
            readOnly: readOnly,
            onChanged: onChanged,
          ),
        ),
      ),
    );
  }

  testWidgets('shows selected contact name and email', (tester) async {
    await pumpField(tester, value: 'c2', onChanged: (_) {});
    expect(find.text('Снабжение · supply@corp.example'), findsOneWidget);
  });

  testWidgets('empty value shows muted None subtitle', (tester) async {
    await pumpField(tester, value: null, onChanged: (_) {});
    expect(find.text('Получатель'), findsOneWidget);
    expect(find.text('Нет'), findsOneWidget);
  });

  testWidgets('pick pushes selection page and returns chosen row id',
      (tester) async {
    String? saved;
    await pumpField(tester, value: null, onChanged: (v) => saved = v);
    await tester.tap(find.byType(ListTile));
    await tester.pumpAndSettle();
    expect(find.text('Клиент'), findsOneWidget);
    await tester.tap(find.text('Клиент'));
    await tester.pumpAndSettle();
    expect(saved, 'c1');
  });

  testWidgets('readOnly tile ignores taps', (tester) async {
    var opened = false;
    await pumpField(tester, value: null, readOnly: true, onChanged: (_) => opened = true);
    await tester.tap(find.byType(ListTile));
    await tester.pump();
    expect(opened, isFalse);
  });
}
