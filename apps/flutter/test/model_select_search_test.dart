import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/features/employee/project_chat_model_select_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

const _models = <Map<String, dynamic>>[
  {
    'id': 'gpt-5-mini',
    'label': 'GPT-5 mini',
    'publisher': 'OpenAI',
    'input_price_usd_per_mtok': 0.15,
    'output_price_usd_per_mtok': 1.25,
    'max_context_tokens': 128000,
  },
  {'id': 'claude-sonnet-4', 'label': 'Claude Sonnet 4', 'publisher': 'Anthropic'},
  {'id': 'gemini-2-flash', 'label': 'Gemini 2 Flash', 'publisher': 'Google'},
];

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

Widget _host({
  required ValueChanged<String?> onPicked,
  List<Map<String, dynamic>> models = _models,
  String? selectedModelId,
}) {
  return Builder(
    builder: (context) => Center(
      child: ElevatedButton(
        onPressed: () async {
          final picked = await ProjectChatModelSelectPage.push(
            context,
            models: models,
            selectedModelId: selectedModelId,
          );
          onPicked(picked);
        },
        child: const Text('open'),
      ),
    ),
  );
}

Future<void> _openPage(WidgetTester tester) async {
  await tester.pumpWidget(themed(_host(onPicked: (_) {})));
  await tester.tap(find.text('open'));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('page renders all model rows and search field', (tester) async {
    await _openPage(tester);

    expect(find.text('Select model'), findsOneWidget);
    expect(find.text('GPT-5 mini'), findsOneWidget);
    expect(find.text('Claude Sonnet 4'), findsOneWidget);
    expect(find.text('Gemini 2 Flash'), findsOneWidget);
    expect(find.byIcon(Icons.search), findsOneWidget);
    // Clear button hidden while the query is empty.
    expect(find.byIcon(Icons.close), findsNothing);
  });

  testWidgets('search filters rows by publisher (case-insensitive)', (
    tester,
  ) async {
    await _openPage(tester);

    await tester.enterText(find.byType(TextField), 'anthropic');
    await tester.pump();

    expect(find.text('Claude Sonnet 4'), findsOneWidget);
    expect(find.text('GPT-5 mini'), findsNothing);
    expect(find.text('Gemini 2 Flash'), findsNothing);
    // Clear button appears once the query is non-empty.
    expect(find.byIcon(Icons.close), findsOneWidget);
  });

  testWidgets('search with no matches shows picker empty state', (
    tester,
  ) async {
    await _openPage(tester);

    await tester.enterText(find.byType(TextField), 'zzz-no-match');
    await tester.pump();

    expect(find.text('No models available'), findsOneWidget);
    expect(find.text('GPT-5 mini'), findsNothing);
    expect(find.text('Claude Sonnet 4'), findsNothing);
    expect(find.text('Gemini 2 Flash'), findsNothing);
    // Search field stays pinned above the empty state.
    expect(find.byIcon(Icons.search), findsOneWidget);
  });

  testWidgets('clear button restores the full list', (tester) async {
    await _openPage(tester);

    await tester.enterText(find.byType(TextField), 'anthropic');
    await tester.pump();
    expect(find.text('GPT-5 mini'), findsNothing);

    await tester.tap(find.byIcon(Icons.close));
    await tester.pump();

    expect(find.text('GPT-5 mini'), findsOneWidget);
    expect(find.text('Claude Sonnet 4'), findsOneWidget);
    expect(find.text('Gemini 2 Flash'), findsOneWidget);
    expect(find.byIcon(Icons.close), findsNothing);
    expect(find.text('No models available'), findsNothing);
  });

  testWidgets('tapping a filtered row pops with the model id', (tester) async {
    String? picked;
    await tester.pumpWidget(
      themed(
        _host(onPicked: (v) => picked = v, selectedModelId: 'gpt-5-mini'),
      ),
    );
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField), 'gemini');
    await tester.pump();
    await tester.tap(find.text('Gemini 2 Flash'));
    await tester.pumpAndSettle();

    expect(picked, 'gemini-2-flash');
    expect(find.text('Gemini 2 Flash'), findsNothing);
  });

  testWidgets('empty catalog keeps placeholder without search field', (
    tester,
  ) async {
    String? picked;
    await tester.pumpWidget(
      themed(_host(onPicked: (v) => picked = v, models: const [])),
    );
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();

    expect(find.text('Select model'), findsOneWidget);
    expect(find.text('Model'), findsOneWidget);
    expect(find.byType(TextField), findsNothing);

    await tester.pageBack();
    await tester.pumpAndSettle();
    expect(picked, isNull);
  });
}
