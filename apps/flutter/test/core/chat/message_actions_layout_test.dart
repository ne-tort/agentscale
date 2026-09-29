import 'package:flutter/gestures.dart' show PointerDeviceKind;
import 'package:flutter/material.dart';
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
    home: Scaffold(
      body: Align(
        alignment: Alignment.topLeft,
        child: SizedBox(width: 800, child: home),
      ),
    ),
  );
}

void main() {
  testWidgets('copy button is flush against the right edge of the message', (tester) async {
    tester.view.physicalSize = const Size(900, 400);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

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

    final gesture = await tester.createGesture(kind: PointerDeviceKind.mouse);
    await gesture.addPointer(location: Offset.zero);
    addTearDown(gesture.removePointer);
    await tester.pump();
    await gesture.moveTo(tester.getCenter(find.byType(AssistantStreamBlock)));
    await tester.pumpAndSettle();

    final copyBox = find.byWidgetPredicate((w) => w is SizedBox && w.width == 28.0);
    expect(copyBox, findsOneWidget);
    final rect = tester.getRect(copyBox);
    // Row stretches to the full 800px message width: the copy box must end at
    // the right edge (the model cell is a ConstrainedBox, not a flex child -
    // a Flexible would steal half the free space and park the button mid-row).
    expect(rect.right, moreOrLessEquals(800, epsilon: 0.5));
  });

  testWidgets('working indicator renders text with animated dots and no spinner', (tester) async {
    await tester.pumpWidget(_themed(const AgentWorkingIndicator()));
    await tester.pump();

    expect(find.byType(CircularProgressIndicator), findsNothing);
    expect(find.text('agentscale работает'), findsOneWidget);
    // Three animated dots.
    final dots = find.byWidgetPredicate(
      (w) => w is Container && (w.constraints?.minWidth ?? 0) == 3.0,
    );
    expect(dots, findsNWidgets(3));
  });

  testWidgets('inline code background is semi-transparent (selection stays visible)', (tester) async {
    await tester.pumpWidget(_themed(const ChatMarkdownBody(text: 'a `code` b')));
    await tester.pump();

    final rich = find.byType(RichText);
    expect(rich, findsWidgets);
    var foundCodeSpan = false;
    for (final e in rich.evaluate()) {
      InlineSpan? span = (e.widget as RichText).text;
      void walk(InlineSpan s) {
        if (s is TextSpan) {
          final bg = s.style?.backgroundColor;
          if (s.text == 'code' && bg != null) {
            foundCodeSpan = true;
            // RenderParagraph paints the selection tint UNDER per-span
            // backgrounds: an opaque code background would visually swallow
            // the selection highlight.
            expect(bg.alpha, lessThan(255));
          }
          for (final c in s.children ?? const <InlineSpan>[]) {
            walk(c);
          }
        }
      }

      walk(span);
      span = null;
    }
    expect(foundCodeSpan, isTrue);
  });
}
