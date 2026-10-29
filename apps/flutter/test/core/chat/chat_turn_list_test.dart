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
    ChatBlock(kind: 'user', raw: {'text': text, 'created_at': '2026-10-29T10:00:00Z'});

ChatBlock _assistant(String text) =>
    ChatBlock(kind: 'assistant_markdown', raw: {'text': text});

ChatBlock _thinking(String text) => ChatBlock(kind: 'thinking', raw: {'text': text});

ChatBlock _toolCall(String id, String name) => ChatBlock(
  kind: 'tool_call',
  raw: {'id': id, 'name': name, 'input': const <String, dynamic>{}},
);

ChatBlock _approval(String id) => ChatBlock(
  kind: 'approval',
  raw: {'id': id, 'name': 'shell', 'input': const <String, dynamic>{}},
);

void main() {
  testWidgets('one turn shows a single work header and the final answer', (
    tester,
  ) async {
    final blocks = [
      _user('сделай отчёт'),
      _thinking('размышление'),
      _toolCall('c1', 'fs.read'),
      _assistant('промежуточный текст'),
      _assistant('<final_answer># Ответ\n\nГотово.</final_answer>'),
    ];
    await tester.pumpWidget(
      _themed(ChatTurnList(turns: buildChatTurns(blocks))),
    );
    await tester.pumpAndSettle();

    expect(find.textContaining('Работал'), findsOneWidget);
    expect(find.textContaining('2 действия'), findsOneWidget);
    expect(find.text('промежуточный текст'), findsNothing);
    expect(find.textContaining('Ответ'), findsWidgets);

    await tester.tap(find.textContaining('Работал'));
    await tester.pumpAndSettle();
    expect(find.text('промежуточный текст'), findsOneWidget);
  });

  testWidgets('a huge turn stays one collapsed row until expanded', (
    tester,
  ) async {
    final work = <ChatBlock>[
      for (var i = 0; i < 100; i++) _thinking('thinking-$i'),
      _assistant('work-detail'),
    ];
    final blocks = [
      _user('много работы'),
      ...work,
      _assistant('<final_answer>итог</final_answer>'),
    ];
    await tester.pumpWidget(
      _themed(ChatTurnList(turns: buildChatTurns(blocks))),
    );
    await tester.pumpAndSettle();

    expect(find.textContaining('Работал'), findsOneWidget);
    expect(find.text('work-detail'), findsNothing);
    // 101 actions collapse into one row, not 101 children.
    expect(find.byType(ChatTurnList), findsOneWidget);

    await tester.tap(find.textContaining('Работал'));
    await tester.pump();
    expect(find.text('work-detail'), findsOneWidget);
  });

  testWidgets('many turns are virtualized, not built all at once', (
    tester,
  ) async {
    final turns = [
      for (var i = 0; i < 200; i++)
        ChatTurn(
          id: 'turn-$i',
          user: _user('user-$i'),
          work: const [],
          finalText: 'answer-$i',
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

    expect(find.text('user-199'), findsOneWidget);
    expect(find.text('user-0'), findsNothing);
  });

  testWidgets('a reload updates the same turn in place, without duplicates', (
    tester,
  ) async {
    final partial = buildChatTurns([
      _user('вопрос'),
      _assistant('<final_answer>part</final_answer>'),
    ]);
    await tester.pumpWidget(_themed(ChatTurnList(turns: partial)));
    await tester.pumpAndSettle();

    final complete = buildChatTurns([
      _user('вопрос'),
      _thinking('поработал'),
      _assistant('промежуточный'),
      _assistant('<final_answer>final answer</final_answer>'),
    ]);
    await tester.pumpWidget(_themed(ChatTurnList(turns: complete)));
    await tester.pumpAndSettle();

    expect(find.textContaining('Работал'), findsOneWidget);
    expect(find.textContaining('final answer'), findsWidgets);
    expect(find.textContaining('part'), findsNothing);
  });

  testWidgets('a pending approval expands the work log automatically', (
    tester,
  ) async {
    final blocks = [
      _user('запусти'),
      _assistant('WORK_DETAIL'),
      _toolCall('c1', 'fs.read'),
      _approval('a1'),
      _assistant('<final_answer>done</final_answer>'),
    ];
    final pendingTurns = buildChatTurns(
      blocks,
      pendingApprovalIds: {'a1'},
    );
    expect(pendingTurns.length, 1);
    expect(pendingTurns.single.hasPendingApproval, isTrue);
    expect(pendingTurns.single.work.length, 3);
    await tester.pumpWidget(
      _themed(ChatTurnList(turns: pendingTurns)),
    );
    await tester.pumpAndSettle();
    expect(find.byType(ApprovalBlock), findsOneWidget);

    // Once the approval is resolved the same turn collapses back.
    await tester.pumpWidget(
      _themed(ChatTurnList(turns: buildChatTurns(blocks))),
    );
    await tester.pumpAndSettle();
    expect(find.byType(ApprovalBlock), findsNothing);
  });

  testWidgets('a markdown table in the final answer stays one table', (
    tester,
  ) async {
    const table = '| Позиция | Цена |\n|---|---|\n| A | 10 |\n| B | 20 |';
    await tester.pumpWidget(
      _themed(
        ChatTurnList(
          turns: buildChatTurns([
            _user('таблица'),
            _assistant('<final_answer>$table</final_answer>'),
          ]),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.byType(Table), findsOneWidget);
  });

  testWidgets('loading history shows a single spinner', (tester) async {
    final turns = buildChatTurns([
      _user('привет'),
      _assistant('<final_answer>ок</final_answer>'),
    ]);
    await tester.pumpWidget(
      _themed(
        ChatTurnList(
          turns: turns,
          hasMoreHistory: true,
          loadingHistory: true,
          onLoadOlder: () {},
        ),
      ),
    );
    await tester.pump();
    expect(find.byType(CircularProgressIndicator), findsOneWidget);
  });
}




