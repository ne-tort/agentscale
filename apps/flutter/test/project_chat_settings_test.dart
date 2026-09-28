import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/features/employee/project_chat_settings_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

Widget themed(Widget home) {
  return MaterialApp(
    theme: AppTheme.light,
    locale: const Locale('en'),
    supportedLocales: AppLocalizations.supportedLocales,
    localizationsDelegates: const [
      AppLocalizations.delegate,
      GlobalMaterialLocalizations.delegate,
      GlobalWidgetsLocalizations.delegate,
      GlobalCupertinoLocalizations.delegate,
    ],
    home: home,
  );
}

Widget _page({String? cabinetId}) {
  return ProjectChatSettingsPage(
    projectId: 'p1',
    sessionId: 's1',
    title: 'T',
    pinned: false,
    cabinetId: cabinetId,
    onTitleChanged: (_) {},
    onPinnedChanged: (_) {},
  );
}

void main() {
  testWidgets(
    'chat settings shows project settings hub tile when cabinetId set',
    (tester) async {
      await tester.pumpWidget(themed(_page(cabinetId: 'c1')));
      await tester.pumpAndSettle();

      expect(find.text('Project settings'), findsOneWidget);
      expect(find.byIcon(Icons.settings_outlined), findsOneWidget);
    },
  );

  testWidgets('project settings hub tile hidden without cabinetId', (
    tester,
  ) async {
    await tester.pumpWidget(themed(_page()));
    await tester.pumpAndSettle();

    expect(find.text('Project settings'), findsNothing);
    // Only the delete tile uses settings-like icons here; the hub icon is gone.
    expect(find.byIcon(Icons.settings_outlined), findsNothing);
  });
}
