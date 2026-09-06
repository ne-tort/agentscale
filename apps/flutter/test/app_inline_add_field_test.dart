import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/core/widgets/app_inline_add_field.dart';
import 'package:prodavan/l10n/app_localizations.dart';

void main() {
  testWidgets('expanded mode hides title and shows input as title', (tester) async {
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
          body: AppInlineAddField(
            title: 'Добавить базу',
            hintText: 'Добавить базу',
            validator: (raw) => raw.trim().isNotEmpty,
            onSave: (_) async {},
          ),
        ),
      ),
    );

    expect(find.text('Добавить базу'), findsOneWidget);
    await tester.tap(find.text('Добавить базу'));
    await tester.pumpAndSettle();

    expect(find.byType(TextField), findsOneWidget);
    // Collapsed title row is gone — only hint/input remains.
    final titleTexts = find.text('Добавить базу').evaluate();
    expect(titleTexts.length, lessThanOrEqualTo(1));
  });
}
