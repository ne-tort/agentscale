import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/chat/controller/chat_session_controller.dart';
import 'package:prodavan/core/chat/models/chat_block.dart';
import 'package:prodavan/core/chat/widgets/chat_checklist_panel.dart';
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

/// Blocks that derive a checklist (a `todo.write` tool call) vs none.
List<ChatBlock> _todoBlocks() => [
      ChatBlock(
        kind: 'tool_call',
        raw: {
          'id': 'c1',
          'name': 'todo.write',
          'input': {
            'items': [
              {'id': '1', 'content': 'Позиция A', 'status': 'pending'},
              {'id': '2', 'content': 'Позиция B', 'status': 'pending'},
            ],
          },
        },
      ),
    ];

Future<void> _pumpScaffold(WidgetTester tester, {required bool withChecklist}) async {
  final api = _FakeApi();
  final controller = ChatSessionController(
    api: api,
    projectId: 'p1',
    sessionId: 's1',
  );
  if (withChecklist) {
    controller.blocks.addAll(_todoBlocks());
  } else {
    // A rendered user block so the transcript (not the empty placeholder) is
    // laid out in both cases.
    controller.blocks.add(ChatBlock(kind: 'user', raw: {'text': 'hi', 'id': 'u1'}));
  }
  addTearDown(controller.dispose);
  await tester.pumpWidget(
    _themed(
      Scaffold(
        body: ChatScaffold(
          controller: controller,
          api: api,
          chatSendable: true,
          loading: false,
          title: const Text('chat'),
        ),
      ),
    ),
  );
  await tester.pump();
  await tester.pump(const Duration(milliseconds: 100));
}

void main() {
  testWidgets('chat column keeps its width cap whether or not the checklist shows', (tester) async {
    tester.view.physicalSize = const Size(1600, 1000);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    // Without a checklist.
    await _pumpScaffold(tester, withChecklist: false);
    expect(find.byType(ChatChecklistPanel), findsNothing);
    final noChecklist = tester.getSize(find.byType(ChatMessageList));

    // With a checklist: the panel appears, but the chat does NOT widen.
    await _pumpScaffold(tester, withChecklist: true);
    expect(find.byType(ChatChecklistPanel), findsOneWidget);
    final withChecklist = tester.getSize(find.byType(ChatMessageList));

    // Same transcript width in both cases (previously it stretched to full).
    expect(withChecklist.width, noChecklist.width);
    // The transcript keeps its readable cap (900 at this breakpoint).
    expect(withChecklist.width, lessThanOrEqualTo(900));
  });
}
