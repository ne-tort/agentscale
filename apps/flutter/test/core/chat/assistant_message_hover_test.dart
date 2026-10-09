import 'package:flutter/gestures.dart' show PointerDeviceKind;
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

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

Future<TestGesture> _hoverOver(WidgetTester tester, Finder finder) async {
  final gesture = await tester.createGesture(kind: PointerDeviceKind.mouse);
  await gesture.addPointer(location: Offset.zero);
  addTearDown(gesture.removePointer);
  await tester.pump();
  await gesture.moveTo(tester.getCenter(finder));
  await tester.pumpAndSettle();
  return gesture;
}

void main() {
  testWidgets('hover reveals usage row with model / tokens / cost and copies text', (tester) async {
    String? clipboardText;
    tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
      SystemChannels.platform,
      (call) async {
        if (call.method == 'Clipboard.setData') {
          clipboardText = (call.arguments as Map?)?['text'] as String?;
        }
        return null;
      },
    );

    await tester.pumpWidget(
      _themed(
        const AssistantStreamBlock(
          text: 'Hello **markdown**',
          usageRaw: {
            'model': 'model-a',
            'input_tokens': 1234,
            'output_tokens': 567,
            'cost_usd': 0.05,
          },
        ),
      ),
    );
    await tester.pumpAndSettle();

    // No hover — no metadata visible and no space reserved.
    expect(find.textContaining('вход:'), findsNothing);
    expect(find.byIcon(Icons.copy_outlined), findsNothing);

    final gesture = await _hoverOver(tester, find.byType(AssistantStreamBlock));

    expect(find.text('model-a'), findsOneWidget);
    expect(find.textContaining('вход: 1\u00A0234'), findsOneWidget);
    expect(find.textContaining('выход: 567'), findsOneWidget);
    expect(find.textContaining('\$0.05'), findsOneWidget);
    expect(find.byIcon(Icons.copy_outlined), findsOneWidget);

    await tester.tap(find.byIcon(Icons.copy_outlined));
    await tester.pump();
    await tester.pump();

    // Full raw markdown copied; icon swaps to the check mark.
    expect(clipboardText, 'Hello **markdown**');
    expect(find.byIcon(Icons.check), findsOneWidget);

    // Copy confirmation keeps the row visible after the pointer leaves.
    await gesture.moveTo(const Offset(-100, -100));
    await tester.pumpAndSettle();
    expect(find.textContaining('вход: 1\u00A0234'), findsOneWidget);

    // …and resets 2s later (row collapses without hover).
    await tester.pump(const Duration(milliseconds: 2100));
    await tester.pumpAndSettle();
    expect(find.byIcon(Icons.check), findsNothing);
    expect(find.textContaining('вход:'), findsNothing);
  });

  testWidgets('runtime cost wins over the costResolver estimate', (tester) async {
    await tester.pumpWidget(
      _themed(
        AssistantStreamBlock(
          text: 'answer',
          usageRaw: const {
            'model': 'model-a',
            'input_tokens': 10,
            'cost_usd': 0.05,
          },
          costResolver: (model, input, output) => 9.9,
        ),
      ),
    );
    await tester.pumpAndSettle();

    await _hoverOver(tester, find.byType(AssistantStreamBlock));
    expect(find.textContaining('\$0.05'), findsOneWidget);
    expect(find.textContaining('\$9'), findsNothing);
  });

  testWidgets('costResolver estimate is used when runtime cost is missing', (tester) async {
    await tester.pumpWidget(
      _themed(
        AssistantStreamBlock(
          text: 'answer',
          usageRaw: const {'model': 'model-a', 'input_tokens': 1000000, 'output_tokens': 1000000},
          costResolver: (model, input, output) => 1.27,
        ),
      ),
    );
    await tester.pumpAndSettle();

    await _hoverOver(tester, find.byType(AssistantStreamBlock));
    expect(find.textContaining('\$1.27'), findsOneWidget);
    // Both token counters are 1 000 000 → two muted cells with grouping.
    expect(find.textContaining('1\u00A0000\u00A0000'), findsNWidgets(2));
  });

  testWidgets('streaming message shows cursor and no meta row', (tester) async {
    await tester.pumpWidget(
      _themed(
        const AssistantStreamBlock(
          text: 'Partial',
          streaming: true,
          usageRaw: {'model': 'model-a', 'input_tokens': 1},
        ),
      ),
    );
    await tester.pump();

    // Streaming: blinking cursor present, metadata hidden even on hover.
    expect(
      find.descendant(
        of: find.byType(AssistantStreamBlock),
        matching: find.byType(FadeTransition),
      ),
      findsOneWidget,
    );
    final gesture = await tester.createGesture(kind: PointerDeviceKind.mouse);
    await gesture.addPointer(location: Offset.zero);
    addTearDown(gesture.removePointer);
    await tester.pump();
    await gesture.moveTo(tester.getCenter(find.byType(AssistantStreamBlock)));
    // No pumpAndSettle here: the cursor repeats forever by design.
    await tester.pump(const Duration(milliseconds: 200));
    expect(find.textContaining('вход:'), findsNothing);
    expect(find.byIcon(Icons.copy_outlined), findsNothing);
  });

  testWidgets('missing usage fields never render anything', (tester) async {
    await tester.pumpWidget(
      _themed(const AssistantStreamBlock(text: 'plain answer')),
    );
    await tester.pumpAndSettle();

    await _hoverOver(tester, find.byType(AssistantStreamBlock));
    expect(find.textContaining('вход:'), findsNothing);
    expect(find.byIcon(Icons.copy_outlined), findsNothing);
  });

  testWidgets('cache cell shows read tokens and hit rate for [OI] usage (input includes cache)', (tester) async {
    await tester.pumpWidget(
      _themed(
        const AssistantStreamBlock(
          text: 'answer',
          // [OI]-compat: input_tokens ALREADY includes the cached subset.
          usageRaw: {
            'model': 'grok-4.7',
            'provider': 'openai',
            'input_tokens': 1000,
            'output_tokens': 100,
            'cache_read_tokens': 400,
          },
        ),
      ),
    );
    await tester.pumpAndSettle();

    await _hoverOver(tester, find.byType(AssistantStreamBlock));
    // 400 / 1000 = 40% (input already IS the full prompt context).
    expect(find.textContaining('кэш: 400 (40%)'), findsOneWidget);
  });

  testWidgets('cache cell adds read+creation into the context for Anthropic usage', (tester) async {
    await tester.pumpWidget(
      _themed(
        const AssistantStreamBlock(
          text: 'answer',
          // Anthropic: input_tokens is cache-exclusive; context = input+read+creation.
          usageRaw: {
            'model': 'claude',
            'provider': 'anthropic',
            'input_tokens': 200,
            'output_tokens': 50,
            'cache_creation_tokens': 500,
            'cache_read_tokens': 300,
          },
        ),
      ),
    );
    await tester.pumpAndSettle();

    await _hoverOver(tester, find.byType(AssistantStreamBlock));
    // read = 300; context = 200 + 300 + 500 = 1000 → 30%.
    expect(find.textContaining('кэш: 300 (30%)'), findsOneWidget);
  });

  testWidgets('no cache cell when there were no cache reads', (tester) async {
    await tester.pumpWidget(
      _themed(
        const AssistantStreamBlock(
          text: 'answer',
          usageRaw: {
            'model': 'model-a',
            'input_tokens': 500,
            'output_tokens': 20,
            'cache_read_tokens': 0,
          },
        ),
      ),
    );
    await tester.pumpAndSettle();

    await _hoverOver(tester, find.byType(AssistantStreamBlock));
    expect(find.textContaining('кэш:'), findsNothing);
  });
}
