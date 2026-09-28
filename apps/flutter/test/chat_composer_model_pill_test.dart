import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/features/employee/widgets/chat_composer.dart';
import 'package:prodavan/l10n/app_localizations.dart';

Widget themed(Widget home) {
  return MaterialApp(
    theme: AppTheme.light,
    locale: const Locale('en'),
    supportedLocales: AppLocalizations.supportedLocales,
    localizationsDelegates: const [
      AppLocalizations.delegate,
      GlobalMaterialLocalizations.delegate,
      GlobalWidgetsLocalizations.delegate,
      GlobalCupertinoLocalizations.delegate,
    ],
    home: home,
  );
}

void main() {
  testWidgets('model pill shows label and fires onPickModel', (tester) async {
    var picked = 0;
    await tester.pumpWidget(
      themed(
        Scaffold(
          body: ChatComposer(
            onSend: (_, _) {},
            modelLabel: 'GPT-5 mini',
            onPickModel: () => picked++,
          ),
        ),
      ),
    );

    expect(find.text('GPT-5 mini'), findsOneWidget);
    expect(find.byIcon(Icons.smart_toy_outlined), findsOneWidget);
    expect(find.byIcon(Icons.expand_more), findsOneWidget);

    await tester.tap(find.text('GPT-5 mini'));
    await tester.pump();
    expect(picked, 1);
  });

  testWidgets('model pill hidden without onPickModel callback', (tester) async {
    await tester.pumpWidget(
      themed(
        Scaffold(
          body: ChatComposer(onSend: (_, _) {}, modelLabel: 'GPT-5 mini'),
        ),
      ),
    );

    expect(find.byIcon(Icons.smart_toy_outlined), findsNothing);
    expect(find.byIcon(Icons.expand_more), findsNothing);
  });

  testWidgets('model pill dimmed and inert while streaming', (tester) async {
    var picked = 0;
    await tester.pumpWidget(
      themed(
        Scaffold(
          body: ChatComposer(
            onSend: (_, _) {},
            modelLabel: 'GPT-5 mini',
            onPickModel: () => picked++,
            streaming: true,
          ),
        ),
      ),
    );

    await tester.tap(find.text('GPT-5 mini'));
    await tester.pump();
    expect(picked, 0);

    final opacity = tester.widget<Opacity>(
      find
          .ancestor(
            of: find.byIcon(Icons.smart_toy_outlined),
            matching: find.byType(Opacity),
          )
          .first,
    );
    expect(opacity.opacity, 0.5);
  });
}
