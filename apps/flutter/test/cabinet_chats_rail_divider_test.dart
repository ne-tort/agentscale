import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/core/widgets/app_taper_hairline.dart';
import 'package:prodavan/features/employee/cabinet_chats_rail.dart';
import 'package:prodavan/l10n/app_localizations.dart';

Widget _app(Widget home) {
  return MaterialApp(
    theme: AppTheme.light,
    locale: const Locale('ru'),
    supportedLocales: AppLocalizations.supportedLocales,
    localizationsDelegates: const [
      AppLocalizations.delegate,
      GlobalMaterialLocalizations.delegate,
      GlobalWidgetsLocalizations.delegate,
      GlobalCupertinoLocalizations.delegate,
    ],
    home: Scaffold(body: home),
  );
}

void main() {
  testWidgets('shows taper divider only when chats block is non-empty', (tester) async {
    await tester.pumpWidget(
      _app(
        const CabinetChatsRail(
          extended: true,
          newChatEnabled: false,
          pinned: [],
          projectChats: [],
          activeSessionId: null,
          onNewChat: null,
          onOpenChat: _noopOpen,
          showLeadingDivider: true,
        ),
      ),
    );
    expect(find.byType(AppTaperHairline), findsNothing);

    await tester.pumpWidget(
      _app(
        CabinetChatsRail(
          extended: true,
          newChatEnabled: true,
          pinned: const [],
          projectChats: const [],
          activeSessionId: null,
          onNewChat: () {},
          onOpenChat: _noopOpen,
          showLeadingDivider: true,
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.byType(AppTaperHairline), findsOneWidget);
  });
}

void _noopOpen(Map<String, dynamic> chat) {}
