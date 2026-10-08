import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/chat/models/chat_block.dart';
import 'package:prodavan/core/chat/models/chat_checklist.dart';
import 'package:prodavan/core/chat/widgets/chat_checklist_panel.dart';
import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/l10n/app_localizations.dart';

ChatBlock _toolCall(String name, Map<String, dynamic> input) => ChatBlock(
      kind: 'tool_call',
      raw: {'id': 'call-$name', 'name': name, 'input': input},
    );

ChatBlock _plan(List<Map<String, dynamic>> tasks) =>
    ChatBlock(kind: 'plan', raw: {'tasks': tasks});

void main() {
  group('deriveChecklistTasks', () {
    test('empty transcript → no checklist', () {
      expect(deriveChecklistTasks(const []), isEmpty);
    });

    test('unrelated blocks alone → no checklist', () {
      final blocks = [
        ChatBlock(kind: 'assistant_markdown', raw: {'text': 'hi'}),
        _toolCall('fs.read', {'path': 'a.txt'}),
      ];
      expect(deriveChecklistTasks(blocks), isEmpty);
    });

    test('todo.write with items is a full replacement', () {
      final blocks = [
        _toolCall('todo.write', {
          'session_id': 's1',
          'items': [
            {'id': '1', 'content': 'Read code', 'status': 'completed'},
            {'id': '2', 'content': 'Write tests', 'status': 'in_progress'},
            {'id': '3', 'content': 'Ship', 'status': 'pending'},
          ],
        }),
      ];
      final tasks = deriveChecklistTasks(blocks);
      expect(tasks.map((t) => t.id), ['1', '2', '3']);
      expect(tasks[0].isCompleted, isTrue);
      expect(tasks[1].isInProgress, isTrue);
      expect(tasks[2].status, 'pending');
      expect(checklistCompletedCount(tasks), 1);
    });

    test('todo.write patch updates by id, keeps order, preserves unknown status', () {
      final blocks = [
        _toolCall('todo.write', {
          'items': [
            {'id': '1', 'content': 'A', 'status': 'pending'},
            {'id': '2', 'content': 'B', 'status': 'pending'},
          ],
        }),
        _toolCall('todo.write', {
          'patch': [
            {'id': '2', 'status': 'completed'},
          ],
        }),
      ];
      final tasks = deriveChecklistTasks(blocks);
      expect(tasks.map((t) => t.id), ['1', '2']);
      expect(tasks[1].status, 'completed');
      expect(tasks[1].title, 'B'); // kept from the previous item
      expect(tasks[0].status, 'pending');
    });

    test('later todo.write items replace the whole list', () {
      final blocks = [
        _toolCall('todo.write', {
          'items': [
            {'id': '1', 'content': 'A', 'status': 'pending'},
          ],
        }),
        _toolCall('todo.write', {
          'items': [
            {'id': '9', 'content': 'Z', 'status': 'in_progress'},
          ],
        }),
      ];
      final tasks = deriveChecklistTasks(blocks);
      expect(tasks.map((t) => t.id), ['9']);
    });

    test('bare and mcp.openclaw-prefixed tool names both match', () {
      for (final name in ['todo.write', 'mcp.openclaw.todo.write']) {
        final tasks = deriveChecklistTasks([
          _toolCall(name, {
            'items': [
              {'id': '1', 'content': 'X', 'status': 'completed'},
            ],
          }),
        ]);
        expect(tasks, hasLength(1), reason: name);
        expect(tasks.first.isCompleted, isTrue, reason: name);
      }
    });

    test('plan block is authoritative and merges with later todo patch', () {
      final blocks = [
        _plan([
          {'title': 'Step 1', 'status': 'done'},
          {'title': 'Step 2', 'status': 'pending'},
        ]),
        _toolCall('todo.write', {
          'patch': [
            {'id': 'task-1', 'status': 'completed'},
          ],
        }),
      ];
      final tasks = deriveChecklistTasks(blocks);
      expect(tasks.map((t) => t.title), ['Step 1', 'Step 2']);
      expect(checklistCompletedCount(tasks), 2);
    });

    test('color tag is stripped from the title and mapped to a color', () {
      final tasks = deriveChecklistTasks([
        _toolCall('todo.write', {
          'items': [
            {'id': '1', 'content': '<warning>Проверить совместимость'},
            {'id': '2', 'content': 'Обычная задача'},
          ],
        }),
      ]);
      expect(tasks[0].title, 'Проверить совместимость');
      expect(tasks[0].color, ChatTaskColor.warning);
      expect(tasks[1].color, isNull);
    });

    test('state tag maps to a rich status (blocked/deferred/partial)', () {
      final tasks = deriveChecklistTasks([
        _toolCall('todo.write', {
          'items': [
            {'id': '1', 'content': '<blocked>Ждём клиента'},
            {'id': '2', 'content': '<postponed>Позже'},
            {'id': '3', 'content': '<partial>Сделано наполовину'},
          ],
        }),
      ]);
      expect(tasks[0].isBlocked, isTrue);
      expect(tasks[1].isDeferred, isTrue);
      expect(tasks[2].isPartial, isTrue);
    });

    test('explicit color field and comment are carried', () {
      final tasks = deriveChecklistTasks([
        _toolCall('todo.write', {
          'items': [
            {'id': '1', 'content': 'Позиция', 'color': 'error', 'comment': 'нет в каталоге'},
          ],
        }),
      ]);
      expect(tasks[0].color, ChatTaskColor.error);
      expect(tasks[0].hasComment, isTrue);
      expect(tasks[0].comment, 'нет в каталоге');
    });

    test('patch preserves color and comment when not overridden', () {
      final blocks = [
        _toolCall('todo.write', {
          'items': [
            {'id': '1', 'content': 'X', 'color': 'info', 'comment': 'c1', 'status': 'pending'},
          ],
        }),
        _toolCall('todo.write', {
          'patch': [
            {'id': '1', 'status': 'in_progress'},
          ],
        }),
      ];
      final tasks = deriveChecklistTasks(blocks);
      expect(tasks[0].isInProgress, isTrue);
      expect(tasks[0].color, ChatTaskColor.info);
      expect(tasks[0].comment, 'c1');
    });
  });

  group('ChatChecklistPanel', () {
    Widget themed(Widget child) => MaterialApp(
          theme: AppTheme.light,
          locale: const Locale('ru'),
          supportedLocales: AppLocalizations.supportedLocales,
          localizationsDelegates: const [
            AppLocalizations.delegate,
            GlobalMaterialLocalizations.delegate,
            GlobalWidgetsLocalizations.delegate,
            GlobalCupertinoLocalizations.delegate,
          ],
          home: Scaffold(body: Align(alignment: Alignment.topRight, child: child)),
        );

    List<ChatChecklistTask> sample(int n, {int done = 0}) => List.generate(
          n,
          (i) => ChatChecklistTask(
            id: '$i',
            title: 'Task $i',
            status: i < done ? 'completed' : 'pending',
          ),
        );

    testWidgets('empty list renders no content', (tester) async {
      await tester.pumpWidget(themed(const ChatChecklistPanel(tasks: [])));
      await tester.pumpAndSettle();
      expect(find.text('Задачи'), findsNothing);
      expect(tester.getSize(find.byType(ChatChecklistPanel)).height, 0);
    });

    testWidgets('expanded shows title, x/y header and tasks', (tester) async {
      await tester.pumpWidget(themed(ChatChecklistPanel(tasks: sample(3, done: 1))));
      await tester.pumpAndSettle();
      expect(find.text('Задачи'), findsOneWidget);
      expect(find.text('1/3'), findsOneWidget);
      expect(find.text('Task 0'), findsOneWidget);
      expect(find.text('Task 2'), findsOneWidget);
    });

    testWidgets('collapsing shows the compact pill and re-expanding restores tasks', (tester) async {
      await tester.pumpWidget(themed(ChatChecklistPanel(tasks: sample(4, done: 2))));
      await tester.pumpAndSettle();

      // Tap the header to collapse.
      await tester.tap(find.text('Задачи'));
      await tester.pumpAndSettle();

      // Compact pill: "2/4 задач выполнено"; tasks hidden.
      expect(find.textContaining('2/4 задач'), findsOneWidget);
      expect(find.text('Task 0'), findsNothing);

      // Tap the pill to expand again.
      await tester.tap(find.textContaining('2/4 задач'));
      await tester.pumpAndSettle();
      expect(find.text('Task 0'), findsOneWidget);
    });

    testWidgets('compact pill is short, not stretched to full height', (tester) async {
      await tester.pumpWidget(themed(ChatChecklistPanel(tasks: sample(3, done: 1), initialExpanded: false)));
      await tester.pumpAndSettle();
      final size = tester.getSize(find.byType(ChatChecklistPanel));
      // A pill is one text line — well under 60px, never a full column.
      expect(size.height, lessThan(60));
      expect(find.textContaining('1/3'), findsOneWidget);
    });

    testWidgets('expanded card grows with content but stays within the cap', (tester) async {
      // Few tasks → card is shorter than the cap (content-sized).
      await tester.pumpWidget(themed(ChatChecklistPanel(tasks: sample(2))));
      await tester.pumpAndSettle();
      final small = tester.getSize(find.byType(ChatChecklistPanel));
      expect(small.height, lessThan(kChatChecklistMaxHeight));
      expect(small.width, kChatChecklistWidth);

      // Many tasks → capped at the max (inner scroll handles the rest).
      await tester.pumpWidget(themed(ChatChecklistPanel(tasks: sample(80))));
      await tester.pumpAndSettle();
      final big = tester.getSize(find.byType(ChatChecklistPanel));
      expect(big.height, lessThanOrEqualTo(kChatChecklistMaxHeight));
      expect(big.height, greaterThan(small.height));
    });

    testWidgets('long list is bounded and scrolls internally', (tester) async {
      await tester.pumpWidget(themed(ChatChecklistPanel(tasks: sample(60, done: 5))));
      await tester.pumpAndSettle();

      // The card never exceeds the cap.
      final size = tester.getSize(find.byType(ChatChecklistPanel));
      expect(size.height, lessThanOrEqualTo(kChatChecklistMaxHeight));

      // A task far down the list is not built until scrolled to.
      expect(find.text('Task 59'), findsNothing);
      await tester.drag(find.byType(ListView), const Offset(0, -4000));
      await tester.pumpAndSettle();
      expect(find.text('Task 59'), findsOneWidget);
    });

    testWidgets('comment is hidden until the task is tapped, then revealed', (tester) async {
      final tasks = [
        const ChatChecklistTask(
          id: '1',
          title: 'Позиция X',
          status: 'blocked',
          comment: 'ждём ответ клиента',
        ),
      ];
      await tester.pumpWidget(themed(ChatChecklistPanel(tasks: tasks)));
      await tester.pumpAndSettle();

      expect(find.text('Позиция X'), findsOneWidget);
      expect(find.text('ждём ответ клиента'), findsNothing);

      await tester.tap(find.text('Позиция X'));
      await tester.pumpAndSettle();
      expect(find.text('ждём ответ клиента'), findsOneWidget);
    });
  });
}
