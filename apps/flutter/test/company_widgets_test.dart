import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/features/company/company_management_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

void main() {
  testWidgets('CompanyManagementPage lists parity sections', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light,
        localizationsDelegates: const [
          AppLocalizations.delegate,
          GlobalMaterialLocalizations.delegate,
          GlobalWidgetsLocalizations.delegate,
          GlobalCupertinoLocalizations.delegate,
        ],
        supportedLocales: AppLocalizations.supportedLocales,
        locale: const Locale('ru'),
        home: const CompanyManagementPage(companyId: 'co_test'),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Сотрудники'), findsOneWidget);
    expect(find.text('AI-ключи'), findsOneWidget);
    expect(find.text('Проекты'), findsOneWidget);
    expect(find.text('Кабинеты'), findsOneWidget);
  });
}
