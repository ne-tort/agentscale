import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/chat/models/chat_block.dart';
import 'package:prodavan/core/chat/models/chat_turn.dart';
import 'package:prodavan/core/chat/widgets/chat_turn_list.dart';
import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/l10n/app_localizations.dart';

Widget _themed(Widget child) => MaterialApp(
  theme: AppTheme.light,
  locale: const Locale('ru'),
  supportedLocales: AppLocalizations.supportedLocales,
  localizationsDelegates: const [
    AppLocalizations.delegate,
    GlobalMaterialLocalizations.delegate,
    GlobalWidgetsLocalizations.delegate,
    GlobalCupertinoLocalizations.delegate,
  ],
  home: Scaffold(
    body: Center(
      child: SizedBox(width: 800, height: 600, child: child),
    ),
  ),
);

void main() {
  testWidgets('long history opens at the newest turn (bottom)', (tester) async {
    final turns = [
      for (var i = 0; i < 40; i++)
        ChatTurn(
          id: 't$i',
          user: ChatBlock(kind: 'user', raw: {'text': 'вопрос $i'}),
          work: const [],
          finalText: 'ответ $i',
          notices: const [],
          running: false,
          actionCount: 0,
          durationMs: null,
          startedAt: null,
          diffStats: null,
          hasPendingApproval: false,
        ),
    ];
    await tester.pumpWidget(_themed(ChatTurnList(turns: turns)));
    await tester.pumpAndSettle();

    final newest = tester.getRect(find.text('вопрос 39'));
    final oldest = find.text('вопрос 0');
    debugPrint('NEWEST=$newest OLDEST_FOUND=${oldest.evaluate().length}');
    expect(newest.bottom, greaterThan(400));
    expect(oldest, findsNothing);
  });
}
