import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/chat/models/chat_block.dart';
import 'package:prodavan/core/chat/widgets/blocks/chat_block_renderer.dart';
import 'package:prodavan/core/chat/widgets/blocks/chat_blocks.dart';
import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/l10n/app_localizations.dart';

Widget _themed(Widget home) {
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
    home: Scaffold(body: Center(child: home)),
  );
}

/// Records `getSidechainTranscript` calls.
class _RecordingApi extends Fake implements ProdavanApi {
  _RecordingApi({this.body});

  final Map<String, dynamic>? body;
  final List<({String projectId, String sessionId, String toolUseId})> calls = [];

  @override
  Future<Map<String, dynamic>> getSidechainTranscript({
    required String projectId,
    required String sessionId,
    required String toolUseId,
  }) async {
    calls.add((projectId: projectId, sessionId: sessionId, toolUseId: toolUseId));
    return body ?? const <String, dynamic>{};
  }
}

void main() {
  testWidgets('collapsed subagent shows title, result summary and done icon', (tester) async {
    await tester.pumpWidget(
      _themed(
        const SubagentBlock(
          title: 'researcher',
          events: [
            {'type': 'text_delta', 'data': {'text': 'partial live event'}},
          ],
          resultSummary: 'Найдено 3 файла',
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('researcher'), findsOneWidget);
    expect(find.text('Найдено 3 файла'), findsOneWidget);
    expect(find.byIcon(Icons.check_circle_outline), findsOneWidget);
    // Collapsed: live events stay hidden until expanded.
    expect(find.textContaining('partial live event'), findsNothing);
  });

  testWidgets('running subagent shows a spinner instead of the done icon', (tester) async {
    await tester.pumpWidget(
      _themed(const SubagentBlock(title: 'worker', running: true)),
    );
    await tester.pump();

    expect(find.byType(CircularProgressIndicator), findsOneWidget);
    expect(find.byIcon(Icons.check_circle_outline), findsNothing);
  });

  testWidgets('expanded subagent renders the fetched sidechain as a mini transcript', (tester) async {
    var calls = 0;
    await tester.pumpWidget(
      _themed(
        SubagentBlock(
          title: 'sub',
          events: const [
            {'type': 'text_delta', 'data': {'text': 'partial live event'}},
          ],
          onFetchSidechain: () async {
            calls++;
            return const [
              {'kind': 'assistant_markdown', 'text': 'Subagent answer'},
              {'kind': 'tool_call', 'id': 't1', 'name': 'Read', 'input': <String, dynamic>{}},
              {'kind': 'tool_result', 'id': 't1', 'name': 'Read', 'output': 'ok'},
            ];
          },
        ),
      ),
    );
    await tester.pumpAndSettle();

    await tester.tap(find.text('sub'));
    await tester.pumpAndSettle();

    expect(calls, 1);
    expect(find.textContaining('Subagent answer'), findsOneWidget);
    expect(find.byType(ToolActivityBlock), findsOneWidget);
    expect(find.text('Прочитан файл'), findsOneWidget);
    // Sidechain loaded: no live-events fallback and no raw toString dump.
    expect(find.textContaining('partial live event'), findsNothing);
    expect(find.textContaining('{type:'), findsNothing);

    // Collapse + re-expand: finished subagent → cached transcript, no refetch.
    await tester.tap(find.text('sub'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('sub'));
    await tester.pumpAndSettle();
    expect(calls, 1);
    expect(find.textContaining('Subagent answer'), findsOneWidget);
  });

  testWidgets('fetch failure shows offline label plus live events fallback', (tester) async {
    await tester.pumpWidget(
      _themed(
        SubagentBlock(
          title: 'sub',
          events: const [
            {'type': 'text_delta', 'data': {'text': 'working hard'}},
          ],
          onFetchSidechain: () async => throw Exception('sidechain offline'),
        ),
      ),
    );
    await tester.pumpAndSettle();

    await tester.tap(find.text('sub'));
    await tester.pumpAndSettle();

    expect(find.textContaining('контейнер не запущен'), findsOneWidget);
    expect(find.textContaining('text_delta · working hard'), findsOneWidget);
  });

  testWidgets('renderer fetches the sidechain by parent_tool_use_id, not agent id', (tester) async {
    final api = _RecordingApi(body: const {'blocks': []});
    await tester.pumpWidget(
      _themed(
        ChatBlockRenderer(
          block: ChatBlock(
            kind: 'subagent',
            raw: {
              'id': 'a1',
              'agent_id': 'a1',
              'agent_type': 'general-purpose',
              'parent_tool_use_id': 'tu1',
              'events': const <dynamic>[],
            },
          ),
          projectId: 'p1',
          sessionId: 's1',
          api: api,
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('general-purpose'), findsOneWidget);

    await tester.tap(find.text('general-purpose'));
    await tester.pumpAndSettle();

    expect(api.calls, hasLength(1));
    expect(api.calls.single.toolUseId, 'tu1');
    expect(api.calls.single.projectId, 'p1');
    expect(api.calls.single.sessionId, 's1');
  });

  testWidgets('renderer marks a streaming-turn subagent as running', (tester) async {
    await tester.pumpWidget(
      _themed(
        ChatBlockRenderer(
          block: ChatBlock(
            kind: 'subagent',
            raw: {
              'id': 'a1',
              'agent_id': 'a1',
              'parent_tool_use_id': 'tu1',
              'events': const <dynamic>[],
            },
          ),
          turnStreaming: true,
        ),
      ),
    );
    await tester.pump();
    expect(find.byType(CircularProgressIndicator), findsOneWidget);

    // Turn finished (default turnStreaming=false + result_summary) →
    // spinner swaps for the done icon and the collapsed summary line.
    await tester.pumpWidget(
      _themed(
        ChatBlockRenderer(
          block: ChatBlock(
            kind: 'subagent',
            raw: {
              'id': 'a1',
              'agent_id': 'a1',
              'parent_tool_use_id': 'tu1',
              'result_summary': 'done',
              'events': const <dynamic>[],
            },
          ),
        ),
      ),
    );
    await tester.pump();
    expect(find.byType(CircularProgressIndicator), findsNothing);
    expect(find.byIcon(Icons.check_circle_outline), findsOneWidget);
    expect(find.text('done'), findsOneWidget);
  });

  testWidgets('running subagent polls the sidechain transcript and stops when done', (tester) async {
    var calls = 0;
    Future<List<Map<String, dynamic>>> fetch() async {
      calls++;
      return [
        {'kind': 'assistant_markdown', 'text': 'tick $calls'},
      ];
    }

    await tester.pumpWidget(
      _themed(SubagentBlock(title: 'sub', running: true, onFetchSidechain: fetch)),
    );
    await tester.pump();

    await tester.tap(find.text('sub'));
    await tester.pump();
    await tester.pump();
    expect(calls, 1);
    expect(find.textContaining('tick 1'), findsOneWidget);

    // 2.5 s poll interval → second fetch, transcript updates live.
    await tester.pump(const Duration(milliseconds: 2600));
    await tester.pump();
    expect(calls, 2);
    expect(find.textContaining('tick 2'), findsOneWidget);

    // Turn finished: polling stops, no further fetches.
    await tester.pumpWidget(
      _themed(SubagentBlock(title: 'sub', running: false, onFetchSidechain: fetch)),
    );
    await tester.pump(const Duration(milliseconds: 6000));
    await tester.pump(const Duration(milliseconds: 6000));
    expect(calls, 2);
  });

  testWidgets('renderer shows the model as title and never the bare "default"', (tester) async {
    await tester.pumpWidget(
      _themed(
        ChatBlockRenderer(
          block: ChatBlock(
            kind: 'subagent',
            raw: {
              'id': 'a1',
              'agent_type': 'default',
              'model': 'gpt-6-astra',
              'task': 'Найти аналоги',
              'parent_tool_use_id': 'tu1',
              'events': const <dynamic>[],
            },
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('gpt-6-astra'), findsOneWidget);
    expect(find.text('default'), findsNothing);
    expect(find.textContaining('agent.spawn'), findsNothing);
  });

  testWidgets('renderer falls back to the task (not "default") when no model', (tester) async {
    await tester.pumpWidget(
      _themed(
        ChatBlockRenderer(
          block: ChatBlock(
            kind: 'subagent',
            raw: {
              'id': 'a1',
              'agent_type': 'default',
              'task': 'Собрать спецификацию',
              'parent_tool_use_id': 'tu1',
              'events': const <dynamic>[],
            },
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.textContaining('Собрать спецификацию'), findsOneWidget);
    expect(find.text('default'), findsNothing);
  });

  testWidgets('subagent stops spinning once its own stop event arrives, even while the parent streams', (tester) async {
    await tester.pumpWidget(
      _themed(
        ChatBlockRenderer(
          block: ChatBlock(
            kind: 'subagent',
            raw: {
              'id': 'a1',
              'parent_tool_use_id': 'tu1',
              'status': 'completed',
              'result_summary': 'готово',
              'events': const <dynamic>[],
            },
          ),
          turnStreaming: true,
        ),
      ),
    );
    await tester.pump();
    expect(find.byType(CircularProgressIndicator), findsNothing);
    expect(find.byIcon(Icons.check_circle_outline), findsOneWidget);
  });

  testWidgets('no progress bar on periodic refresh — only on first load', (tester) async {
    var calls = 0;
    Future<List<Map<String, dynamic>>> fetch() async {
      calls++;
      return [
        {'kind': 'assistant_markdown', 'text': 'tick $calls'},
      ];
    }

    await tester.pumpWidget(
      _themed(SubagentBlock(title: 'sub', running: true, onFetchSidechain: fetch)),
    );
    await tester.pump();
    await tester.tap(find.text('sub'));
    // First load: indeterminate bar is allowed while it resolves.
    await tester.pump();
    await tester.pump();
    expect(calls, 1);

    // Second poll: the bar must NOT reappear (was the "blinking" bar bug).
    await tester.pump(const Duration(milliseconds: 2600));
    await tester.pump();
    expect(calls, 2);
    expect(find.byType(LinearProgressIndicator), findsNothing);
  });

  testWidgets('expanded subagent shows a meta row with the model', (tester) async {
    await tester.pumpWidget(
      _themed(
        SubagentBlock(
          title: 'gpt-6-astra',
          model: 'gpt-6-astra',
          usageRaw: const {'input_tokens': 1234, 'output_tokens': 56},
          turnMs: 4200,
          resultSummary: 'готово',
          onFetchSidechain: () async => const [
            {'kind': 'assistant_markdown', 'text': 'answer'},
          ],
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.text('gpt-6-astra'));
    await tester.pumpAndSettle();

    expect(find.textContaining('вход: 1\u00A0234'), findsOneWidget);
    expect(find.textContaining('выход: 56'), findsOneWidget);
    expect(find.text('0:04'), findsOneWidget);
  });
}
