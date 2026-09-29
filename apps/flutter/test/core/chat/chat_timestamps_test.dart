import 'dart:async';

import 'package:flutter/gestures.dart' show PointerDeviceKind;
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:intl/intl.dart' show DateFormat;

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/chat/controller/chat_session_controller.dart';
import 'package:prodavan/core/chat/models/chat_block.dart';
import 'package:prodavan/core/chat/models/chat_projection.dart';
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

Widget _themedWide(Widget home) {
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
    home: Scaffold(
      body: Align(
        alignment: Alignment.topLeft,
        child: SizedBox(width: 800, child: home),
      ),
    ),
  );
}

Future<TestGesture> _hoverOver(WidgetTester tester, Finder finder) async {
  final gesture = await tester.createGesture(kind: PointerDeviceKind.mouse);
  await gesture.addPointer(location: Offset.zero);
  addTearDown(gesture.removePointer);
  await tester.pump();
  await gesture.moveTo(tester.getCenter(finder));
  await tester.pumpAndSettle();
  return gesture;
}

class _FakeApi extends Fake implements ProdavanApi {
  _FakeApi({this.stream});

  final StreamController<Map<String, dynamic>>? stream;

  @override
  ProjectChatStreamHandle projectChatStream({
    required String projectId,
    required String text,
    String? sessionId,
    String? model,
    List<String> attachmentRefs = const [],
  }) {
    final stream = this.stream;
    if (stream == null) {
      throw StateError('no fake stream configured');
    }
    return ProjectChatStreamHandle(
      stream: stream.stream,
      abort: () {},
    );
  }

  @override
  Future<List<Map<String, dynamic>>> listPendingApprovals({
    required String projectId,
    required String sessionId,
  }) async {
    return const [];
  }
}

void main() {
  group('live turn timestamps', () {
    test('send stamps the optimistic user block; turn end stamps the assistant block', () async {
      final events = StreamController<Map<String, dynamic>>();
      final api = _FakeApi(stream: events);
      final controller = ChatSessionController(
        api: api,
        projectId: 'prj_ts',
        sessionId: 'sess_ts',
      );

      final sendFuture = controller.send('hello');
      await pumpEventQueue();

      final user = controller.visibleBlocks.firstWhere((b) => b.kind == 'user');
      final userTs = user.raw['created_at'] as String?;
      expect(userTs, isNotNull);
      final parsedUserTs = DateTime.tryParse(userTs!);
      expect(parsedUserTs, isNotNull);
      // UTC ISO — same shape the server persists for history reloads.
      expect(parsedUserTs!.isUtc, isTrue);

      events.add({'type': 'text_delta', 'data': {'text': 'answer'}});
      events.add({'type': 'done', 'data': {'reason': 'completed'}});
      await events.close();
      await sendFuture;
      await pumpEventQueue();

      final assistant = controller.visibleBlocks.lastWhere((b) => b.kind == 'assistant_markdown');
      final assistantTs = assistant.raw['created_at'] as String?;
      expect(assistantTs, isNotNull);
      expect(DateTime.tryParse(assistantTs!), isNotNull);
      final turnMs = assistant.raw['turn_ms'];
      expect(turnMs, isA<int>());
      expect(turnMs as int, greaterThanOrEqualTo(0));
      controller.dispose();
    });

    test('turn without assistant output skips duration and completion time', () async {
      final events = StreamController<Map<String, dynamic>>();
      final api = _FakeApi(stream: events);
      final controller = ChatSessionController(
        api: api,
        projectId: 'prj_ts_err',
        sessionId: 'sess_ts_err',
      );

      final sendFuture = controller.send('hello');
      await pumpEventQueue();
      events.add({'type': 'error', 'data': {'code': 'X', 'message': 'boom'}});
      await events.close();
      await sendFuture;
      await pumpEventQueue();

      expect(controller.visibleBlocks.where((b) => b.kind == 'assistant_markdown'), isEmpty);
      // The optimistic user message keeps its send time.
      final user = controller.visibleBlocks.firstWhere((b) => b.kind == 'user');
      expect(user.raw['created_at'], isA<String>());
      controller.dispose();
    });
  });

  group('projection timestamp passthrough', () {
    test('user_message echo preserves the optimistic created_at', () {
      var blocks = <ChatBlock>[
        createLiveBlock('user', {'text': 'hi', 'created_at': '2026-09-29T10:00:00Z'}),
      ];
      blocks = applyStreamEvent(blocks, {
        'type': 'user_message',
        'data': {'text': 'hi', 'id': 'srv_1'},
      });
      expect(blocks.single.kind, 'user');
      expect(blocks.single.raw['created_at'], '2026-09-29T10:00:00Z');
    });

    test('chatBlocksFromTranscript passes created_at/turn_ms through to blocks', () {
      final blocks = chatBlocksFromTranscript([
        {'kind': 'user', 'text': 'q', 'created_at': '2026-09-29T10:00:00+00:00'},
        {
          'kind': 'assistant_markdown',
          'text': 'a',
          'created_at': '2026-09-29T10:00:45+00:00',
          'turn_ms': 45000,
        },
      ]);
      expect(blocks[0].raw['created_at'], '2026-09-29T10:00:00+00:00');
      expect(blocks[1].raw['created_at'], '2026-09-29T10:00:45+00:00');
      expect(blocks[1].raw['turn_ms'], 45000);
    });
  });

  group('message timestamp widgets', () {
    testWidgets('assistant completion time is always visible; duration/copy only on hover', (tester) async {
      await tester.pumpWidget(
        _themed(
          AssistantStreamBlock(
            text: 'Hello',
            usageRaw: const {'model': 'model-a', 'input_tokens': 1234},
            completedAt: DateTime(2026, 9, 29, 14, 7),
            turnMs: 45000,
          ),
        ),
      );
      await tester.pumpAndSettle();

      // No hover: the time is permanent; metadata, duration and copy hidden.
      expect(find.text('14:07'), findsOneWidget);
      expect(find.textContaining('вход:'), findsNothing);
      expect(find.text('0:45'), findsNothing);
      expect(find.byIcon(Icons.copy_outlined), findsNothing);

      // Hover: usage cells + duration + copy join the time on the same row.
      await _hoverOver(tester, find.byType(AssistantStreamBlock));
      expect(find.textContaining('вход:'), findsOneWidget);
      expect(find.text('0:45'), findsOneWidget);
      expect(find.byIcon(Icons.copy_outlined), findsOneWidget);

      // Time sits on the LEFT of the metadata group.
      final timeRect = tester.getRect(find.text('14:07'));
      final modelRect = tester.getRect(find.text('model-a'));
      expect(timeRect.right, lessThan(modelRect.left));
    });

    testWidgets('time stays visible after the copied confirmation expires', (tester) async {
      await tester.pumpWidget(
        _themed(
          AssistantStreamBlock(
            text: 'Hello',
            usageRaw: const {'model': 'model-a', 'input_tokens': 1234},
            completedAt: DateTime(2026, 9, 29, 14, 7),
            turnMs: 83000,
          ),
        ),
      );
      await tester.pumpAndSettle();

      final gesture = await _hoverOver(tester, find.byType(AssistantStreamBlock));
      expect(find.text('1:23'), findsOneWidget);

      // Pointer leaves and the 2s copied window is not even armed — the
      // metadata collapses, the time must remain.
      await gesture.moveTo(const Offset(-100, -100));
      await tester.pumpAndSettle();
      expect(find.text('14:07'), findsOneWidget);
      expect(find.textContaining('вход:'), findsNothing);
    });

    testWidgets('duration formats as m:ss (tabular, no units)', (tester) async {
      await tester.pumpWidget(
        _themed(
          AssistantStreamBlock(
            text: 'Hello',
            completedAt: DateTime(2026, 9, 29, 14, 7),
            turnMs: 45000,
          ),
        ),
      );
      await tester.pumpAndSettle();
      await _hoverOver(tester, find.byType(AssistantStreamBlock));
      expect(find.text('0:45'), findsOneWidget);
    });

    testWidgets('revealing the copy icon does not shift the message layout', (tester) async {
      tester.view.physicalSize = const Size(900, 400);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);

      await tester.pumpWidget(
        _themedWide(
          AssistantStreamBlock(
            text: 'Hello',
            usageRaw: const {'model': 'model-a', 'input_tokens': 1234},
            completedAt: DateTime(2026, 9, 29, 14, 7),
            turnMs: 45000,
          ),
        ),
      );
      await tester.pumpAndSettle();

      // Idle: the metadata row is already mounted (time anchor) at its
      // fixed height; the copy button is NOT in the tree yet.
      final bodyIdle = tester.getRect(find.text('Hello'));
      expect(find.byIcon(Icons.copy_outlined), findsNothing);

      // Hover reveals usage + copy — the message body must not move.
      await _hoverOver(tester, find.byType(AssistantStreamBlock));
      expect(find.byIcon(Icons.copy_outlined), findsOneWidget);
      final bodyHover = tester.getRect(find.text('Hello'));
      expect(bodyHover.top, moreOrLessEquals(bodyIdle.top, epsilon: 0.01));
      expect(bodyHover.bottom, moreOrLessEquals(bodyIdle.bottom, epsilon: 0.01));

      // The copy button is flush right and the reserved slot keeps its box.
      final copyBox =
          find.byWidgetPredicate((w) => w is SizedBox && w.width == 28.0);
      expect(copyBox, findsWidgets);
      expect(tester.getRect(copyBox.last).right, moreOrLessEquals(800, epsilon: 0.5));

      // Pointer leaves: the row keeps its height (time anchor), the message
      // still has not moved a single pixel.
      final gesture = await tester.createGesture(kind: PointerDeviceKind.mouse);
      await gesture.moveTo(const Offset(-100, -100));
      await tester.pumpAndSettle();
      final bodyLeft = tester.getRect(find.text('Hello'));
      expect(bodyLeft.top, moreOrLessEquals(bodyIdle.top, epsilon: 0.01));
      expect(find.text('14:07'), findsOneWidget);
    });

    testWidgets('user message shows send time under the bubble, right-aligned', (tester) async {
      tester.view.physicalSize = const Size(900, 400);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);

      await tester.pumpWidget(
        _themedWide(
          SizedBox(
            width: 800,
            child: UserMessageBlock(text: 'hi', timestamp: DateTime(2026, 9, 29, 10, 5)),
          ),
        ),
      );
      await tester.pumpAndSettle();

      final time = find.text('10:05');
      expect(time, findsOneWidget);
      final bubble = find
          .ancestor(of: find.text('hi'), matching: find.byType(Container))
          .first;
      final timeRect = tester.getRect(time);
      final bubbleRect = tester.getRect(bubble);
      // Under the bubble…
      expect(timeRect.top, greaterThan(bubbleRect.bottom - 1));
      // …flush to the same right edge as the bubble.
      expect(timeRect.right, moreOrLessEquals(bubbleRect.right, epsilon: 0.5));
      expect(timeRect.right, moreOrLessEquals(800, epsilon: 0.5));
    });

    testWidgets('user message without timestamp renders no time label', (tester) async {
      await tester.pumpWidget(
        _themedWide(const UserMessageBlock(text: 'hi')),
      );
      await tester.pumpAndSettle();
      expect(find.byWidgetPredicate((w) => w is Text && _looksLikeClock(w.data)), findsNothing);
    });

    testWidgets('ChatBlockRenderer wires raw created_at/turn_ms into the blocks', (tester) async {
      await tester.pumpWidget(
        _themed(
          ChatBlockRenderer(
            block: ChatBlock.fromJson(const {
              'kind': 'assistant_markdown',
              'text': 'answer',
              'created_at': '2026-09-29T07:05:00Z',
              'turn_ms': 45000,
              'usage': {'model': 'm', 'input_tokens': 1, 'output_tokens': 2},
            }),
          ),
        ),
      );
      await tester.pumpAndSettle();

      // Server ts is UTC; the label is the local HH:MM of the same moment.
      final expected = DateFormat('HH:mm').format(DateTime.parse('2026-09-29T07:05:00Z').toLocal());
      expect(find.text(expected), findsOneWidget);
      expect(find.text('0:45'), findsNothing); // duration is hover-only

      await _hoverOver(tester, find.byType(AssistantStreamBlock));
      expect(find.text('0:45'), findsOneWidget);
      expect(find.text('m'), findsOneWidget);
    });

    testWidgets('ChatBlockRenderer wires user created_at into the timestamp label', (tester) async {
      await tester.pumpWidget(
        _themed(
          ChatBlockRenderer(
            block: ChatBlock.fromJson(const {
              'kind': 'user',
              'text': 'hi',
              'created_at': '2026-09-29T07:05:00Z',
            }),
          ),
        ),
      );
      await tester.pumpAndSettle();

      final expected = DateFormat('HH:mm').format(DateTime.parse('2026-09-29T07:05:00Z').toLocal());
      expect(find.text(expected), findsOneWidget);
    });
  });
}

bool _looksLikeClock(String? data) => data != null && RegExp(r'^\d{1,2}:\d{2}$').hasMatch(data);
