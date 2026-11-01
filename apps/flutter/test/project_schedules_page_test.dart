import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:prodavan/core/auth/token_session.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/features/employee/project_schedules_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';
import 'package:shared_preferences/shared_preferences.dart';

class _ScheduleBackend {
  _ScheduleBackend({List<Map<String, dynamic>> initialSchedules = const []})
      : schedules = initialSchedules
            .map((item) => <String, dynamic>{...item})
            .toList();

  final List<Map<String, Object?>> requests = [];
  final List<Map<String, dynamic>> schedules;

  Future<http.Response> handle(http.Request request) async {
    final body = request.body.isEmpty
        ? <String, dynamic>{}
        : jsonDecode(request.body) as Map<String, dynamic>;
    requests.add({
      'method': request.method,
      'path': request.url.path,
      'body': body,
    });
    final path = request.url.path;
    if (request.method == 'GET' && path == '/api/v1/projects/proj-1/schedules') {
      return _json({'items': schedules});
    }
    if (request.method == 'POST' && path == '/api/v1/projects/proj-1/schedules') {
      final item = <String, dynamic>{...body, 'id': 'schedule-1'};
      schedules.add(item);
      return _json(item);
    }
    final match = RegExp(r'^/api/v1/projects/proj-1/schedules/([^/]+)$')
        .firstMatch(path);
    if (match != null) {
      final id = match.group(1);
      final index = schedules.indexWhere((item) => item['id'] == id);
      if (index < 0) return _json({'detail': 'not found'}, statusCode: 404);
      if (request.method == 'PATCH') {
        schedules[index].addAll(body);
        return _json(schedules[index]);
      }
      if (request.method == 'DELETE') {
        schedules.removeAt(index);
        return _json({'ok': true});
      }
    }
    return _json({'detail': 'unexpected request'}, statusCode: 404);
  }

  http.Response _json(Object body, {int statusCode = 200}) => http.Response(
        jsonEncode(body),
        statusCode,
        headers: const {'content-type': 'application/json'},
      );
}

Widget _app() => MaterialApp(
      locale: const Locale('en'),
      supportedLocales: AppLocalizations.supportedLocales,
      localizationsDelegates: const [
        AppLocalizations.delegate,
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      home: const ProjectSchedulesPage(projectId: 'proj-1'),
    );

Future<void> _installAuthSession() async {
  SharedPreferences.setMockInitialValues(<String, Object>{});
  final secureStore = <String, String>{};
  const channel = MethodChannel('plugins.it_nomads.com/flutter_secure_storage');
  TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
      .setMockMethodCallHandler(channel, (call) async {
    final args = (call.arguments ?? const <String, Object?>{}) as Map<Object?, Object?>;
    final key = args['key'] as String? ?? '';
    switch (call.method) {
      case 'write':
        secureStore[key] = args['value'] as String? ?? '';
        return null;
      case 'read':
        return secureStore[key];
      case 'delete':
        secureStore.remove(key);
        return null;
      case 'deleteAll':
        secureStore.clear();
        return null;
      default:
        return null;
    }
  });
  await tokenSession.applyTokens(
    baseUrl: 'http://backend.test/api/v1',
    accessToken: 'test-token',
    expiresAt: DateTime.now().add(const Duration(hours: 1)),
  );
  workContext.enterCabinet('cab-1');
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('shows capped missed-run warning after outage metadata is reloaded', (tester) async {
    await _installAuthSession();
    final backend = _ScheduleBackend(initialSchedules: [
      {
        'id': 'schedule-1',
        'name': 'Daily report',
        'cron': '0 9 * * *',
        'timezone': 'Europe/Samara',
        'enabled': true,
        'payload': {
          'kind': 'report',
          'missed_count_capped': true,
          'missed_count_lower_bound': 3,
        },
      },
    ]);
    await http.runWithClient(
      () async {
        await tester.pumpWidget(_app());
        await tester.pumpAndSettle();
        expect(
          find.textContaining('Catch-up was limited after an outage; at least 3 runs were missed.'),
          findsOneWidget,
        );
      },
      () => MockClient(backend.handle),
    );
  });

  testWidgets('lists and serializes create, toggle, edit, and delete', (tester) async {
    await _installAuthSession();
    final backend = _ScheduleBackend();
    await http.runWithClient(
      () async {
        await tester.pumpWidget(_app());
        await tester.pumpAndSettle();
        expect(find.text('No schedules yet'), findsOneWidget);

        await tester.tap(find.byTooltip('Add schedule'));
        await tester.pumpAndSettle();
        await tester.enterText(find.widgetWithText(TextFormField, 'Name'), 'Daily report');
        await tester.enterText(find.widgetWithText(TextFormField, 'Cron expression'), '15 8 * * 1-5');
        await tester.enterText(find.widgetWithText(TextFormField, 'Timezone'), 'Europe/Samara');
        await tester.enterText(
          find.widgetWithText(TextFormField, 'Payload (JSON object)'),
          '{"kind":"report","attempt":2}',
        );
        await tester.tap(find.widgetWithText(FilledButton, 'Save'));
        await tester.pumpAndSettle();

        expect(find.text('Daily report'), findsOneWidget);
        expect(backend.requests.first, {
          'method': 'GET',
          'path': '/api/v1/projects/proj-1/schedules',
          'body': <String, dynamic>{},
        });
        expect(backend.requests.where((r) => r['method'] == 'POST').single, {
          'method': 'POST',
          'path': '/api/v1/projects/proj-1/schedules',
          'body': {
            'name': 'Daily report',
            'cron': '15 8 * * 1-5',
            'timezone': 'Europe/Samara',
            'payload': {'kind': 'report', 'attempt': 2},
            'enabled': true,
          },
        });

        await tester.tap(find.byType(Switch));
        await tester.pumpAndSettle();
        expect(tester.widget<Switch>(find.byType(Switch)).value, isFalse);
        expect(backend.requests.where((r) => r['method'] == 'PATCH').first, {
          'method': 'PATCH',
          'path': '/api/v1/projects/proj-1/schedules/schedule-1',
          'body': {'enabled': false},
        });

        await tester.tap(find.text('Daily report'));
        await tester.pumpAndSettle();
        await tester.enterText(find.widgetWithText(TextFormField, 'Name'), 'Weekly report');
        await tester.enterText(
          find.widgetWithText(TextFormField, 'Payload (JSON object)'),
          '{"kind":"weekly","channels":["email"]}',
        );
        await tester.tap(find.widgetWithText(FilledButton, 'Save'));
        await tester.pumpAndSettle();
        final editRequest = backend.requests.where((r) => r['method'] == 'PATCH').last;
        expect(editRequest, {
          'method': 'PATCH',
          'path': '/api/v1/projects/proj-1/schedules/schedule-1',
          'body': {
            'name': 'Weekly report',
            'cron': '15 8 * * 1-5',
            'timezone': 'Europe/Samara',
            'payload': {'kind': 'weekly', 'channels': ['email']},
            'enabled': false,
          },
        });

        await tester.tap(find.byTooltip('Schedule actions'));
        await tester.pumpAndSettle();
        await tester.tap(find.text('Delete').last);
        await tester.pumpAndSettle();
        await tester.tap(find.widgetWithText(FilledButton, 'Delete'));
        await tester.pumpAndSettle();
        expect(find.text('No schedules yet'), findsOneWidget);
        expect(backend.requests.where((r) => r['method'] == 'DELETE').single, {
          'method': 'DELETE',
          'path': '/api/v1/projects/proj-1/schedules/schedule-1',
          'body': <String, dynamic>{},
        });
        expect(backend.schedules, isEmpty);
        final listRequests = backend.requests.where((r) => r['method'] == 'GET');
        expect(listRequests, hasLength(5));
        expect(
          listRequests.every((r) => r['path'] == '/api/v1/projects/proj-1/schedules'),
          isTrue,
        );
        await tester.pumpWidget(const SizedBox.shrink());
      },
      () => MockClient(backend.handle),
    );
  });
}
