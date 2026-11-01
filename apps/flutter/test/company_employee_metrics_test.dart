import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:prodavan/core/api/company_api.dart';
import 'package:prodavan/core/auth/token_session.dart';
import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/features/company/company_employee_detail_page.dart';
import 'package:prodavan/features/company/company_employees_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('getEmployeeMetrics requests the company employee metrics endpoint', () async {
    late Uri requested;
    await _installAuthSession();
    await http.runWithClient(
      () async {
        final api = CompanyApi(
          baseUrl: 'http://backend.test/api/v1',
          bearerToken: 'test-token',
        );
        final result = await api.getEmployeeMetrics(
          companyId: 'company-1',
          employeeId: 'employee-2',
        );
        expect(result['agent_tokens_used'], 1234);
        expect(result['agent_requests'], 7);
      },
      () => _RecordingClient((request) {
        requested = request.url;
        return http.Response(
          jsonEncode({
            'employee_id': 'employee-2',
            'agent_tokens_used': 1234,
            'agent_requests': 7,
          }),
          200,
          headers: {'content-type': 'application/json'},
        );
      }),
    );

    expect(
      requested.toString(),
      'http://backend.test/api/v1/companies/company-1/employees/employee-2/metrics',
    );
  });

  test('restoreEmployee requests the backend employee restore endpoint', () async {
    late Uri requested;
    await _installAuthSession();
    await http.runWithClient(
      () async {
        final api = CompanyApi(
          baseUrl: 'http://backend.test/api/v1',
          bearerToken: 'test-token',
        );
        final result = await api.restoreEmployee(
          companyId: 'company-1',
          employeeId: 'employee-2',
        );
        expect(result['restored'], isTrue);
      },
      () => _RecordingClient((request) {
        requested = request.url;
        expect(request.method, 'POST');
        return http.Response(
          jsonEncode({'id': 'employee-2', 'restored': true, 'status': 'disabled', 'paused': true}),
          200,
          headers: {'content-type': 'application/json'},
        );
      }),
    );

    expect(
      requested.toString(),
      'http://backend.test/api/v1/employees/employee-2/restore',
    );
  });

  testWidgets('recycle bin restores through employee URL and removes tombstone after reload', (tester) async {
    await _installAuthSession();
    companyContext.setSession(
      baseUrl: 'http://backend.test/api/v1',
      bearerToken: 'test-token',
    );
    companyContext.selectCompany(id: 'company-1', name: 'Test Company');
    final requested = <Uri>[];
    var restoreCalls = 0;
    var restored = false;

    await http.runWithClient(
      () async {
        await tester.pumpWidget(
          MaterialApp(
            theme: AppTheme.light,
            locale: const Locale('ru'),
            supportedLocales: AppLocalizations.supportedLocales,
            localizationsDelegates: const [
              AppLocalizations.delegate,
              GlobalMaterialLocalizations.delegate,
              GlobalWidgetsLocalizations.delegate,
              GlobalCupertinoLocalizations.delegate,
            ],
            home: const CompanyEmployeesPage(companyId: 'company-1'),
          ),
        );
        await tester.pumpAndSettle();

        await tester.tap(find.text('Корзина'));
        await tester.pumpAndSettle();
        expect(find.text('deleted-worker'), findsOneWidget);
        expect(find.byTooltip('Восстановить сотрудника'), findsOneWidget);

        await tester.tap(find.byTooltip('Восстановить сотрудника').first);
        await tester.pumpAndSettle();
        expect(find.text('Восстановить сотрудника'), findsOneWidget);
        await tester.tap(find.text('Восстановить сотрудника').last);
        await tester.pumpAndSettle();

        expect(find.text('deleted-worker'), findsNothing);
        expect(find.text('Корзина пуста'), findsOneWidget);
        await tester.pumpWidget(const SizedBox.shrink());
      },
      () => _RecordingClient((request) {
        requested.add(request.url);
        if (request.method == 'POST') {
          restoreCalls += 1;
          expect(request.url.toString(),
              'http://backend.test/api/v1/employees/employee-2/restore');
          restored = true;
          return http.Response(
            jsonEncode({'id': 'employee-2', 'restored': true}),
            200,
            headers: {'content-type': 'application/json'},
          );
        }
        final isRecycleBin = request.url.queryParameters['include_deleted'] == 'true';
        final items = !restored && isRecycleBin
            ? [
                {
                  'id': 'employee-2',
                  'login': 'deleted-worker',
                  'deleted_at': '2026-01-01T00:00:00Z',
                  'status': 'disabled',
                },
              ]
            : const <Map<String, dynamic>>[];
        return http.Response(
          jsonEncode({'items': items}),
          200,
          headers: {'content-type': 'application/json'},
        );
      }),
    );

    expect(restoreCalls, 1);
    expect(
      requested,
      contains(Uri.parse(
          'http://backend.test/api/v1/companies/company-1/employees?include_deleted=true')),
    );
  });

  testWidgets('employee detail shows token and request usage metrics', (tester) async {
    await _installAuthSession();
    companyContext.setSession(
      baseUrl: 'http://backend.test/api/v1',
      bearerToken: 'test-token',
    );
    companyContext.selectCompany(id: 'company-1', name: 'Test Company');
    await http.runWithClient(
      () async {
        await tester.pumpWidget(
          MaterialApp(
            theme: AppTheme.light,
            locale: const Locale('ru'),
            supportedLocales: AppLocalizations.supportedLocales,
            localizationsDelegates: const [
              AppLocalizations.delegate,
              GlobalMaterialLocalizations.delegate,
              GlobalWidgetsLocalizations.delegate,
              GlobalCupertinoLocalizations.delegate,
            ],
            home: const CompanyEmployeeDetailPage(
              companyId: 'company-1',
              employeeId: 'employee-2',
              employeeLogin: 'worker',
            ),
          ),
        );
        await tester.pumpAndSettle();
        expect(find.text('Токены'), findsOneWidget);
        expect(find.text('AI-Запросы'), findsOneWidget);
        expect(find.text('1234'), findsOneWidget);
        expect(find.text('7'), findsOneWidget);
        await tester.pumpWidget(const SizedBox.shrink());
      },
      () => _RecordingClient((request) => http.Response(
        jsonEncode({
          'employee_id': 'employee-2',
          'agent_tokens_used': 1234,
          'agent_requests': 7,
        }),
        200,
        headers: {'content-type': 'application/json'},
      )),
    );
  });
}

Future<void> _installAuthSession() async {
  SharedPreferences.setMockInitialValues(<String, Object>{});
  final secureStore = <String, String>{};
  const channel = MethodChannel('plugins.it_nomads.com/flutter_secure_storage');
  TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
      .setMockMethodCallHandler(channel, (call) async {
    final args = (call.arguments ?? const <String, Object?>{})
        as Map<Object?, Object?>;
    final key = args['key'] as String?;
    switch (call.method) {
      case 'write':
        secureStore[key ?? ''] = args['value'] as String? ?? '';
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
}

class _RecordingClient extends http.BaseClient {
  _RecordingClient(this.handler);

  final http.Response Function(http.Request request) handler;

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    final response = handler(request as http.Request);
    return http.StreamedResponse(
      Stream<List<int>>.value(response.bodyBytes),
      response.statusCode,
      headers: response.headers,
      request: request,
    );
  }
}
