import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart' show LogicalKeyboardKey;
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/features/employee/widgets/chat_composer.dart';
import 'package:prodavan/l10n/app_localizations.dart';

class _Api extends ProdavanApi {
  _Api() : super(baseUrl: 'http://test', bearerToken: 't');

  final List<Map<String, Object?>> uploads = [];

  @override
  Future<Map<String, dynamic>> uploadProjectAttachment({
    required String projectId,
    required String filename,
    required List<int> bytes,
    String? contentType,
  }) async {
    uploads.add({'projectId': projectId, 'filename': filename, 'bytes': bytes.length});
    return {'id': 'att_${uploads.length}'};
  }

  @override
  Future<Map<String, dynamic>> getProjectComposerDraft({
    required String projectId,
  }) async => throw StateError('no draft');

  @override
  Future<Map<String, dynamic>> getSessionComposerDraft({
    required String projectId,
    required String sessionId,
  }) async => throw StateError('no draft');
}

Widget _themed(Widget home) {
  return MaterialApp(
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

void main() {
  testWidgets('attachDroppedFiles uploads through the shared pipeline', (tester) async {
    final api = _Api();
    final sent = <List<String>>[];
    await tester.pumpWidget(
      _themed(
        ChatComposer(
          projectId: 'proj_1',
          api: api,
          onSend: (text, refs) => sent.add(refs),
        ),
      ),
    );
    await tester.pumpAndSettle();

    final state = tester.state<State>(find.byType(ChatComposer));
    expect(state, isA<ChatComposerState>());

    await (state as ChatComposerState).attachDroppedFiles([
      DroppedChatFile(name: 'a.csv', bytes: Uint8List.fromList([1, 2, 3])),
      DroppedChatFile(name: 'b.xlsx', bytes: Uint8List.fromList([4])),
    ]);
    await tester.pumpAndSettle();

    expect(api.uploads.length, 2);
    expect(api.uploads.first['filename'], 'a.csv');
    expect(api.uploads.last['filename'], 'b.xlsx');
    // Both pending chips show up in the composer.
    expect(find.text('a.csv'), findsOneWidget);
    expect(find.text('b.xlsx'), findsOneWidget);

    // The send flow carries the attachment ids (submit via Enter key).
    await tester.enterText(find.byType(TextField), 'обработай');
    await tester.sendKeyEvent(LogicalKeyboardKey.enter);
    await tester.pump();
    expect(sent, isNotEmpty);
    expect(sent.last, ['att_1', 'att_2']);
  });

  testWidgets('attachDroppedFiles is a no-op while disabled', (tester) async {
    final api = _Api();
    await tester.pumpWidget(
      _themed(
        ChatComposer(
          projectId: 'proj_1',
          api: api,
          enabled: false,
          onSend: (_, __) {},
        ),
      ),
    );
    await tester.pumpAndSettle();
    final state = tester.state<ChatComposerState>(find.byType(ChatComposer));
    await state.attachDroppedFiles([
      DroppedChatFile(name: 'x.csv', bytes: Uint8List.fromList([1])),
    ]);
    await tester.pumpAndSettle();
    expect(api.uploads, isEmpty);
  });
}
