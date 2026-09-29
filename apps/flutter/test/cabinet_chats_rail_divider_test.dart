import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/theme/app_spacing.dart';
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
  testWidgets('section break appears only when the chats block is non-empty', (tester) async {
    // Empty tree and no legacy tile — nothing to separate.
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
    expect(find.text('Проекты'), findsNothing);
    expect(find.byType(AppTaperHairline), findsNothing);

    // Legacy global tile renders → the section break appears with it.
    await tester.pumpWidget(
      _app(
        CabinetChatsRail(
          extended: true,
          legacyLayout: true,
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
    expect(find.text('Проекты'), findsOneWidget);
  });

  testWidgets('section break shows when a project branch has chats', (tester) async {
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
    expect(find.text('A1'), findsOneWidget);

    final label = find.text('Проекты');
    expect(label, findsOneWidget);
    // Full vertical breathing room above the break, half below (md / 2).
    final paddings = tester
        .widgetList<Padding>(
          find.ancestor(of: label, matching: find.byType(Padding)),
        )
        .map((p) => p.padding)
        .toList();
    expect(
      paddings,
      contains(EdgeInsets.only(top: AppSpacing.md, bottom: AppSpacing.md / 2)),
    );
    // The fading line runs on both sides of the label.
    final row = tester
        .widget<Row>(find.ancestor(of: label, matching: find.byType(Row)).first)
        .children;
    expect(row.whereType<Expanded>(), hasLength(2));
  });

  testWidgets('section break shows for an empty branch (draft row only)', (tester) async {
    await tester.pumpWidget(
      _app(
        CabinetChatsRail(
          extended: true,
          newChatEnabled: false,
          projectGroups: [
            _group('proj_1', 'Alpha', newChatEnabled: false, chats: const []),
          ],
          activeSessionId: null,
          onNewChat: null,
          onNewChatForProject: (_) {},
          onOpenChat: _noopOpen,
          showLeadingDivider: true,
        ),
      ),
    );
    await tester.pumpAndSettle();
    // The draft row makes the chats block non-empty → the section break shows.
    expect(find.text('Новый диалог'), findsOneWidget);
    expect(find.text('Проекты'), findsOneWidget);
  });

  testWidgets('icon-only rail keeps a plain fading divider', (tester) async {
    await tester.pumpWidget(
      _app(
        CabinetChatsRail(
          extended: false,
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
    // Collapsed rail: no room for a label — just the fading hairline.
    expect(find.byType(AppTaperHairline), findsOneWidget);
    expect(find.text('Проекты'), findsNothing);
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
