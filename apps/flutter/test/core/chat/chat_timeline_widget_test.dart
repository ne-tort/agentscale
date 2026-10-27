import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/chat/models/chat_block.dart';
import 'package:prodavan/core/chat/widgets/blocks/chat_blocks.dart';
import 'package:prodavan/core/chat/widgets/chat_nav_rail.dart';
import 'package:prodavan/core/chat/widgets/chat_scaffold.dart';
import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/l10n/app_localizations.dart';

class _FakeApi extends Fake implements ProdavanApi {}

Widget _themed(Widget home) => MaterialApp(
  theme: AppTheme.light,
  locale: const Locale('ru'),
  supportedLocales: AppLocalizations.supportedLocales,
  localizationsDelegates: const [
    AppLocalizations.delegate,
    GlobalMaterialLocalizations.delegate,
    GlobalWidgetsLocalizations.delegate,
    GlobalCupertinoLocalizations.delegate,
  ],
  home: home,
);

ChatBlock _toolCall(
  String name, {
  required String id,
  Map<String, dynamic>? input,
}) => ChatBlock(
  kind: 'tool_call',
  raw: {'id': id, 'name': name, 'input': input ?? const {}},
);

ChatBlock _toolResult(String id, {Object? output}) => ChatBlock(
  kind: 'tool_result',
  raw: {'id': id, 'name': 'tool', 'output': output},
);

ChatBlock _user(String text) => ChatBlock(kind: 'user', raw: {'text': text});

ChatBlock _assistant(String text) =>
    ChatBlock(kind: 'assistant_markdown', raw: {'text': text});

Future<void> _pumpList(WidgetTester tester, List<ChatBlock> blocks) async {
  tester.view.physicalSize = const Size(1200, 900);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  await tester.pumpWidget(
    _themed(
      Scaffold(
        body: ChatMessageList(
          blocks: blocks,
          api: _FakeApi(),
          projectId: 'p1',
          sessionId: 's1',
          turnStreaming: false,
        ),
      ),
    ),
  );
  await tester.pumpAndSettle();
}

void main() {
  testWidgets(
    'final answer is open, all actions collapse into one activity line',
    (tester) async {
      final blocks = [
        _user('q'),
        _toolCall('edit', id: 't1', input: {'path': 'lib/a.py'}),
        _toolResult('t1', output: 'diff --git a/lib/a.py\n@@ -1 +1 @@\n+x'),
        _toolCall('glob', id: 't2', input: {'globPattern': '*.dart'}),
        _toolResult('t2', output: 'a.dart'),
        _assistant('Final answer'),
      ];
      await _pumpList(tester, blocks);

      // Final answer is visible.
      expect(find.textContaining('Final answer'), findsOneWidget);
      // Actions are collapsed behind one line.
      expect(find.textContaining('Работал'), findsOneWidget);
      expect(find.textContaining('lib/a.py'), findsNothing);

      await tester.tap(find.textContaining('Работал'));
      await tester.pumpAndSettle();
      expect(find.textContaining('lib/a.py'), findsOneWidget);
      expect(find.textContaining('*.dart'), findsOneWidget);
    },
  );

  testWidgets(
    'expanded activity stays expanded when the agent adds another tool',
    (tester) async {
      final blocks = [
        _user('q'),
        _toolCall('edit', id: 't1', input: {'path': 'lib/a.py'}),
        _toolResult('t1'),
        _toolCall('glob', id: 't2', input: {'globPattern': '*.dart'}),
        _toolResult('t2'),
        _assistant('Final answer'),
      ];
      await _pumpList(tester, blocks);
      await tester.tap(find.textContaining('Работал'));
      await tester.pumpAndSettle();
      expect(find.textContaining('lib/a.py'), findsOneWidget);

      // New action arrives after the answer → it becomes activity again; the
      // activity header key is stable, so the expanded state must survive.
      final next = [
        ...blocks,
        _toolCall('delete', id: 't3', input: {'path': 'lib/b.py'}),
        _toolResult('t3'),
      ];
      await _pumpList(tester, next);
      expect(find.textContaining('lib/a.py'), findsOneWidget);
      expect(find.textContaining('lib/b.py'), findsOneWidget);
    },
  );

  testWidgets('600 blocks render without duplicate-key corruption', (
    tester,
  ) async {
    final blocks = <ChatBlock>[_user('start')];
    for (var i = 0; i < 200; i++) {
      blocks.add(_toolCall('edit', id: 'c$i', input: {'path': 'f$i.py'}));
      blocks.add(_toolResult('c$i', output: 'ok $i'));
      if (i % 20 == 0) blocks.add(_assistant('progress $i'));
    }
    blocks.add(_assistant('done'));
    await _pumpList(tester, blocks);
    expect(tester.takeException(), isNull);
    expect(find.textContaining('done'), findsOneWidget);
    expect(find.textContaining('Работал'), findsOneWidget);
  });

  testWidgets(
    'streaming chunker preserves every line across chunk boundaries',
    (tester) async {
      final text = List.generate(
        200,
        (i) => 'line ${i.toString().padLeft(3, '0')} ${'word ' * 12}',
      ).join('\n');
      await tester.pumpWidget(
        _themed(
          Scaffold(
            body: SingleChildScrollView(
              child: AssistantStreamBlock(text: text, streaming: true),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      final plain = tester
          .widgetList<RichText>(
            find.descendant(
              of: find.byType(AssistantStreamBlock),
              matching: find.byType(RichText),
            ),
          )
          .map((w) => w.text.toPlainText())
          .join(' ');
      String norm(String s) => s.replaceAll(RegExp(r'\s+'), ' ').trim();
      expect(norm(plain), norm(text));
    },
  );

  testWidgets(
    'streaming markdown keeps fenced code intact (blank lines included)',
    (tester) async {
      final text =
          'Intro\n\n```dart\nvoid main() {\n\n  print("hi");\n\n}\n```\n\nOutro';
      await tester.pumpWidget(
        _themed(
          Scaffold(
            body: SingleChildScrollView(
              child: AssistantStreamBlock(text: text, streaming: true),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.textContaining('print'), findsOneWidget);
      expect(find.textContaining('Outro'), findsOneWidget);
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets('large tool outputs and a large final answer stay responsive', (
    tester,
  ) async {
    final bigOutput = 'line of tool output\n' * 8000; // ~160 KB
    final blocks = <ChatBlock>[_user('big task')];
    for (var i = 0; i < 5; i++) {
      blocks.add(_toolCall('read', id: 'big$i', input: {'path': 'f$i.txt'}));
      blocks.add(_toolResult('big$i', output: bigOutput));
    }
    final answer = StringBuffer('Final answer\n\n');
    for (var i = 0; i < 800; i++) {
      answer.writeln(
        'Paragraph $i: the agent explains a long result with **bold** and `code`.',
      );
      answer.writeln();
    }
    blocks.add(_assistant(answer.toString()));
    await _pumpList(tester, blocks);
    expect(tester.takeException(), isNull);
    // The list is pinned to the bottom: the tail of the huge answer is built
    // with incremental markdown, not the whole transcript.
    expect(find.textContaining('Paragraph 799'), findsOneWidget);
  });

  testWidgets('a large activity expands without formatting every tool panel', (
    tester,
  ) async {
    final bigOutput = 'line of tool output\n' * 8000; // ~160 KB
    final blocks = <ChatBlock>[_user('big task')];
    const tools = ['read', 'glob', 'grep', 'ls', 'shell'];
    for (var i = 0; i < tools.length; i++) {
      blocks.add(_toolCall(tools[i], id: 'big$i', input: {'path': 'f$i.txt'}));
      blocks.add(_toolResult('big$i', output: bigOutput));
    }
    await _pumpList(tester, blocks);
    expect(find.textContaining('Работал'), findsOneWidget);
    await tester.tap(find.textContaining('Работал'));
    await tester.pumpAndSettle();
    expect(find.textContaining('f0.txt'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('nav rail reveals on hover and jumps to a prompt', (
    tester,
  ) async {
    final prompts = [
      for (var i = 0; i < 5; i++)
        ChatNavPrompt(visualIndex: i * 4, text: 'prompt $i'),
    ];
    int? jumped;
    await tester.pumpWidget(
      _themed(
        Scaffold(
          body: SizedBox(
            width: 400,
            height: 500,
            child: ChatNavRail(
              prompts: prompts,
              entryCount: 20,
              revealed: true,
              onJump: (i) => jumped = i,
            ),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    final rail = tester.widget<AnimatedOpacity>(
      find
          .descendant(
            of: find.byType(ChatNavRail),
            matching: find.byType(AnimatedOpacity),
          )
          .first,
    );
    expect(rail.opacity, 1);

    await tester.tap(find.byKey(const ValueKey('chat-nav-tick-3')));
    await tester.pumpAndSettle();
    expect(jumped, 12);
  });

  testWidgets('nav rail is invisible until revealed', (tester) async {
    await tester.pumpWidget(
      _themed(
        Scaffold(
          body: SizedBox(
            width: 400,
            height: 500,
            child: ChatNavRail(
              prompts: const [ChatNavPrompt(visualIndex: 0, text: 'q')],
              entryCount: 2,
              revealed: false,
              onJump: (_) {},
            ),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    final rail = tester.widget<AnimatedOpacity>(
      find
          .descendant(
            of: find.byType(ChatNavRail),
            matching: find.byType(AnimatedOpacity),
          )
          .first,
    );
    expect(rail.opacity, 0);
  });

  testWidgets(
    'jumping scrolls a long virtualized transcript to the target prompt',
    (tester) async {
      final blocks = <ChatBlock>[];
      for (var i = 0; i < 40; i++) {
        blocks.add(_user('prompt $i'));
        blocks.add(_assistant('answer $i'));
      }
      await _pumpList(tester, blocks);
      final state = tester.state<ChatMessageListState>(
        find.byType(ChatMessageList),
      );
      final future = state.jumpToVisualIndex(79); // the oldest prompt
      await tester.pumpAndSettle();
      await future;
      await tester.pumpAndSettle();
      expect(find.text('prompt 0'), findsOneWidget);
    },
  );

  testWidgets('streaming markdown with a large answer renders incrementally', (
    tester,
  ) async {
    final buffer = StringBuffer();
    for (var i = 0; i < 80; i++) {
      buffer.writeln('Paragraph $i with **bold** text and `code`.');
      buffer.writeln();
    }
    await tester.pumpWidget(
      _themed(
        Scaffold(
          body: SingleChildScrollView(
            child: AssistantStreamBlock(
              text: buffer.toString(),
              streaming: true,
            ),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.textContaining('Paragraph 0'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
