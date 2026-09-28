import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/features/employee/widgets/chat_model_picker_sheet.dart';
import 'package:prodavan/l10n/app_localizations.dart';

const _models = <Map<String, dynamic>>[
  {
    'id': 'm-a',
    'label': 'Model A',
    'input_price_usd_per_mtok': 0.15,
    'output_price_usd_per_mtok': 1.25,
  },
  {'id': 'm-b', 'label': 'Model B', 'output_price_usd_per_mtok': 25},
  {'id': 'm-c', 'label': 'Model C'},
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
  String? defaultModelId,
  bool enabled = true,
}) {
  return Builder(
    builder: (context) => Center(
      child: ElevatedButton(
        onPressed: () async {
          final picked = await showChatModelPickerSheet(
            context,
            models: models,
            selectedModelId: selectedModelId,
            defaultModelId: defaultModelId,
            enabled: enabled,
          );
          onPicked(picked);
        },
        child: const Text('open'),
      ),
    ),
  );
}

void main() {
  testWidgets('sheet renders labels, prices, default badge and selection', (
    tester,
  ) async {
    String? picked;
    await tester.pumpWidget(
      themed(
        _host(
          onPicked: (v) => picked = v,
          selectedModelId: 'm-a',
          defaultModelId: 'm-b',
        ),
      ),
    );
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();

    expect(find.text('Select model'), findsOneWidget);
    expect(find.text('Model A'), findsOneWidget);
    expect(find.text('Model B'), findsOneWidget);
    expect(find.text('Model C'), findsOneWidget);
    // Price subtitle only for models with at least one price; trailing
    // zeros trimmed, null rendered as em dash.
    expect(find.text('0.15 / 1.25 \$ · 1M tokens'), findsOneWidget);
    expect(find.text('— / 25 \$ · 1M tokens'), findsOneWidget);
    expect(find.textContaining('1M tokens'), findsNWidgets(2));
    // Default badge only on the default model.
    expect(find.text('Default'), findsOneWidget);
    // Selection check only on the selected model.
    expect(find.byIcon(Icons.check), findsOneWidget);

    await tester.tap(find.text('Model A'));
    await tester.pumpAndSettle();
    expect(picked, 'm-a');
    expect(find.text('Model A'), findsNothing);
  });

  testWidgets('sheet with empty models shows empty state', (tester) async {
    String? picked;
    await tester.pumpWidget(
      themed(_host(onPicked: (v) => picked = v, models: const [])),
    );
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();

    expect(find.text('Select model'), findsOneWidget);
    expect(find.text('No models available'), findsOneWidget);
    expect(find.byIcon(Icons.close), findsOneWidget);

    await tester.tap(find.byIcon(Icons.close));
    await tester.pumpAndSettle();
    expect(picked, isNull);
  });

  testWidgets('disabled sheet rows are inert', (tester) async {
    String? picked;
    await tester.pumpWidget(
      themed(_host(onPicked: (v) => picked = v, enabled: false)),
    );
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();

    await tester.tap(find.text('Model A'));
    await tester.pump();
    expect(picked, isNull);
    expect(find.text('Model A'), findsOneWidget);
  });
}
