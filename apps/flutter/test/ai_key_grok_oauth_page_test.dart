import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/l10n/app_localizations.dart';

import 'package:prodavan/features/admin/ai_key_integration_type.dart';
import 'package:prodavan/features/company/ai_key_grok_oauth_page.dart';

Widget _wrap(Widget child) => MaterialApp(
      locale: const Locale('ru'),
      supportedLocales: AppLocalizations.supportedLocales,
      localizationsDelegates: const [
        AppLocalizations.delegate,
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      home: child,
    );

Map<String, dynamic> _pending() => const {
      'status': 'pending',
      'user_code': 'GROK-1234',
      'verification_uri': 'https://x.ai/activate',
      'verification_uri_complete': 'https://x.ai/activate?code=GROK-1234',
      'interval_sec': 1,
    };

void main() {
  test('AiKeyIntegrationType: xai_oauth распознаётся и входит в список', () {
    final t = AiKeyIntegrationType.fromKey(provider: 'xai', apiKind: 'xai_oauth');
    expect(t.id, 'xai_oauth');
    expect(t.isXaiOauth, isTrue);
    expect(t.isApiKey, isFalse);
    expect(AiKeyIntegrationType.all, contains(AiKeyIntegrationType.xaiOauth));
    // неизвестный HTTP-вид — как раньше, api_key
    expect(
      AiKeyIntegrationType.fromKey(provider: 'codex', apiKind: 'openai_api').isApiKey,
      isTrue,
    );
  });

  testWidgets('pending: код подтверждения и кнопка ссылки', (tester) async {
    var startCalls = 0;
    await tester.pumpWidget(_wrap(AiKeyGrokOauthPage(
      pollInterval: const Duration(seconds: 30), // не поллим в тесте
      start: () async {
        startCalls += 1;
        return _pending();
      },
      status: () async => _pending(),
    )));
    await tester.pump();
    await tester.pump();

    expect(startCalls, 1);
    expect(find.text('GROK-1234'), findsOneWidget);
    expect(find.text('Открыть страницу авторизации'), findsOneWidget);
    expect(find.text('Ожидаем подтверждения…'), findsOneWidget);
  });

  testWidgets('authorized: страница закрывается с true', (tester) async {
    bool? result;
    var statusCalls = 0;
    await tester.pumpWidget(_wrap(Builder(
      builder: (context) => Scaffold(
        body: ElevatedButton(
          onPressed: () async {
            result = await Navigator.of(context).push<bool>(
              MaterialPageRoute<bool>(
                builder: (_) => AiKeyGrokOauthPage(
                  pollInterval: const Duration(milliseconds: 30),
                  start: () async => _pending(),
                  status: () async {
                    statusCalls += 1;
                    return statusCalls >= 2
                        ? const {'status': 'authorized'}
                        : _pending();
                  },
                ),
              ),
            );
          },
          child: const Text('go'),
        ),
      ),
    )));
    await tester.tap(find.text('go'));
    await tester.pump();
    await tester.pump();
    // первый poll → pending, второй → authorized → pop(true)
    await tester.pump(const Duration(milliseconds: 40));
    await tester.pump(const Duration(milliseconds: 40));
    await tester.pump(const Duration(milliseconds: 40));
    await tester.pumpAndSettle();

    expect(result, isTrue);
  });

  testWidgets('denied: ошибка и перезапуск потока', (tester) async {
    var startCalls = 0;
    await tester.pumpWidget(_wrap(AiKeyGrokOauthPage(
      pollInterval: const Duration(milliseconds: 20),
      start: () async {
        startCalls += 1;
        return _pending();
      },
      status: () async => const {'status': 'denied'},
    )));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 30));
    await tester.pump();

    expect(find.text('Доступ запрещён'), findsOneWidget);
    expect(find.text('Начать заново'), findsOneWidget);

    await tester.tap(find.text('Начать заново'));
    await tester.pump();
    await tester.pump();
    expect(startCalls, 2);
  });
}
