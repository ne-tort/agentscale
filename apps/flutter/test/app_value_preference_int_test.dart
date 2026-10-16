import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/preferences/app_value_preference.dart';
import 'package:prodavan/core/theme/app_theme.dart';

void main() {
  testWidgets('AppValuePreference<int> сохраняет введённое число', (tester) async {
    int? saved;
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light,
        home: Scaffold(
          body: AppValuePreference<int>(
            title: 'Шагов за раз',
            value: 25,
            digitsOnly: true,
            onSave: (v) async => saved = v,
          ),
        ),
      ),
    );

    await tester.tap(find.text('Шагов за раз'));
    await tester.pumpAndSettle();
    expect(find.byType(TextField), findsOneWidget);

    await tester.enterText(find.byType(TextField), '40');
    await tester.testTextInput.receiveAction(TextInputAction.done);
    await tester.pumpAndSettle();

    expect(saved, 40);
  });

  testWidgets('AppValuePreference<String> продолжает работать', (tester) async {
    String? saved;
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light,
        home: Scaffold(
          body: AppValuePreference<String>(
            title: 'Название',
            value: 'старое',
            onSave: (v) async => saved = v,
          ),
        ),
      ),
    );

    await tester.tap(find.text('Название'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField), 'новое');
    await tester.testTextInput.receiveAction(TextInputAction.done);
    await tester.pumpAndSettle();

    expect(saved, 'новое');
  });

  testWidgets('validateInput не даёт сохранить мусор', (tester) async {
    int? saved;
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light,
        home: Scaffold(
          body: AppValuePreference<int>(
            title: 'Шагов за раз',
            value: 25,
            digitsOnly: true,
            validateInput: (raw) {
              final n = int.tryParse(raw);
              return n != null && n >= 1 && n <= 1000;
            },
            onSave: (v) async => saved = v,
          ),
        ),
      ),
    );

    await tester.tap(find.text('Шагов за раз'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField), '');
    await tester.testTextInput.receiveAction(TextInputAction.done);
    await tester.pumpAndSettle();

    expect(saved, isNull);
  });
}
