import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/chat/models/chat_block.dart';
import 'package:prodavan/core/chat/models/chat_turn.dart';
import 'package:prodavan/core/chat/widgets/blocks/chat_blocks.dart';
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
      child: SizedBox(width: 800, height: 700, child: child),
    ),
  ),
);

ChatBlock _user(String text) =>
    ChatBlock(kind: 'user', raw: {'text': text, 'created_at': '2026-10-30T00:00:00Z'});

ChatBlock _answer(String text) =>
    ChatBlock(kind: 'assistant_markdown', raw: {'text': text});

void main() {
  testWidgets('long answer then short answer stays at the bottom', (tester) async {
    final long = List.generate(80, (i) => 'Параграф $i').join('\n\n');
    var turns = buildChatTurns([
      _user('привет'),
      _answer('<final_answer>$long</final_answer>'),
    ]);
    await tester.pumpWidget(_themed(ChatTurnList(turns: turns)));
    await tester.pumpAndSettle();
    var list = tester.getRect(find.byType(ChatTurnList));
    var answer = tester.getRect(find.byType(ChatMarkdownBody));
    debugPrint('LONG list=$list answer=$answer');

    turns = buildChatTurns([
      _user('привет'),
      _answer('<final_answer>ок</final_answer>'),
    ]);
    await tester.pumpWidget(_themed(ChatTurnList(turns: turns)));
    await tester.pumpAndSettle();
    list = tester.getRect(find.byType(ChatTurnList));
    answer = tester.getRect(find.byType(ChatMarkdownBody));
    debugPrint('SHORT list=$list answer=$answer');
    expect(answer.bottom, greaterThan(list.bottom - 100));
  });

  testWidgets('finalizing a long turn snaps a short answer to the bottom', (
    tester,
  ) async {
    final long = List.generate(80, (i) => 'Параграф $i').join('\n\n');
    var turns = buildChatTurns(
      [
        _user('привет'),
        _answer('<final_answer>$long</final_answer>'),
      ],
      turnInProgress: true,
    );
    await tester.pumpWidget(_themed(ChatTurnList(turns: turns)));
    await tester.pumpAndSettle();

    // Read the middle of the long answer (offset > 0), then finalize the turn.
    await tester.drag(find.byType(CustomScrollView), const Offset(0, 500));
    await tester.pumpAndSettle();

    turns = buildChatTurns([
      _user('привет'),
      _answer('<final_answer>ок</final_answer>'),
    ]);
    await tester.pumpWidget(_themed(ChatTurnList(turns: turns)));
    await tester.pumpAndSettle();

    final list = tester.getRect(find.byType(ChatTurnList));
    final answer = tester.getRect(find.byType(ChatMarkdownBody));
    debugPrint('FINALIZE list=$list answer=$answer');
    expect(answer.bottom, greaterThan(list.bottom - 100));
  });

  testWidgets('short answer with load-more button stays at the bottom', (tester) async {
    final turns = buildChatTurns([
      _user('привет'),
      _answer('<final_answer>ок</final_answer>'),
    ]);
    await tester.pumpWidget(
      _themed(
        ChatTurnList(
          turns: turns,
          hasMoreHistory: true,
          loadingHistory: false,
          onLoadOlder: () {},
        ),
      ),
    );
    await tester.pumpAndSettle();
    final list = tester.getRect(find.byType(ChatTurnList));
    final answer = tester.getRect(find.byType(ChatMarkdownBody));
    debugPrint('LOADMORE list=$list answer=$answer');
    expect(answer.bottom, greaterThan(list.bottom - 100));
  });
}

