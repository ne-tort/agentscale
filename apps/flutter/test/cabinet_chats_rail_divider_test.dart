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
          projectGroups: [],
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
          projectGroups: [],
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

  testWidgets('divider shows when a project branch has chats', (tester) async {
    await tester.pumpWidget(
      _app(
        CabinetChatsRail(
          extended: true,
          newChatEnabled: false,
          projectGroups: [
            _group('proj_1', 'Alpha', chats: [_chat('ags_1', 'A1')]),
          ],
          activeSessionId: null,
          onNewChat: null,
          onOpenChat: _noopOpen,
          showLeadingDivider: true,
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.byType(AppTaperHairline), findsOneWidget);
    expect(find.text('A1'), findsOneWidget);
  });
}

Map<String, dynamic> _group(
  String id,
  String name, {
  bool newChatEnabled = false,
  List<Map<String, dynamic>> chats = const [],
}) {
  return <String, dynamic>{
    'project_id': id,
    'project_name': name,
    'status': 'active',
    'new_chat_enabled': newChatEnabled,
    'chats': chats,
  };
}

Map<String, dynamic> _chat(String sid, String title, {bool pinned = false}) {
  return <String, dynamic>{
    'session_id': sid,
    'project_id': 'proj_1',
    'project_name': 'Alpha',
    'title': title,
    'pinned': pinned,
    'has_draft': false,
    'has_messages': true,
    'last_message_at': '2026-06-01T00:00:00+00:00',
    'status': 'active',
    'created_at': '2026-06-01T00:00:00+00:00',
  };
}

void _noopOpen(Map<String, dynamic> chat) {}
