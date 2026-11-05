import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/theme/app_theme.dart';
import 'package:prodavan/core/preferences/app_nav_preference.dart';
import 'package:prodavan/features/company/company_employees_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';
import 'package:prodavan/core/auth/token_session.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets(
    'switches to recycle bin, confirms restore, and removes the tombstone',
    (tester) async {
      final client = _RecycleBinClient();
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
              home: const MediaQuery(
                data: MediaQueryData(size: Size(390, 844)),
                child: CompanyEmployeesPage(companyId: 'company-1'),
              ),
            ),
          );
          await tester.pumpAndSettle();
          expect(find.text('active.worker'), findsOneWidget);
          expect(find.text('deleted.worker'), findsNothing);

          await tester.tap(find.text('Корзина'));
          await tester.pumpAndSettle();
          expect(find.text('deleted.worker'), findsOneWidget);
          expect(find.byTooltip('Восстановить сотрудника'), findsOneWidget);

          await tester.tap(find.byTooltip('Восстановить сотрудника'));
          await tester.pumpAndSettle();
          // The confirm copy names the employee and then says what restore means
          // ("Он будет включен в список приостановленных сотрудников"), so the screen is
          // asserted on the naming rather than on the full sentence: an exact match pins
          // the wording and breaks on any copy edit that keeps the meaning.
          expect(find.textContaining('Восстановить deleted.worker?'), findsOneWidget);

          await tester.tap(find.byType(AppNavPreference));
          await tester.pumpAndSettle();

          expect(client.restoreRequests, 1);
          expect(client.restoreMethod, 'POST');
          expect(client.restorePath, '/api/v1/employees/employee-deleted/restore');
          expect(find.text('deleted.worker'), findsNothing);
          expect(find.text('Корзина пуста'), findsOneWidget);
        },
        () => client,
      );

      expect(client.recycleBinListRequests, greaterThanOrEqualTo(2));
      await tester.pumpWidget(const SizedBox.shrink());
    },
  );
}

class _RecycleBinClient extends http.BaseClient {
  bool tombstoneVisible = true;
  int recycleBinListRequests = 0;
  int restoreRequests = 0;
  String? restoreMethod;
  String? restorePath;

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    final path = request.url.path;
    final isRecycleBin = request.url.queryParameters['include_deleted'] == 'true';
    late http.Response response;

    if (request.method == 'GET' && path.endsWith('/companies/company-1/employees')) {
      if (isRecycleBin) {
        recycleBinListRequests += 1;
      }
      final items = isRecycleBin && tombstoneVisible
          ? <Map<String, dynamic>>[
              {
                'id': 'employee-deleted',
                'login': 'deleted.worker',
                'contact_email': 'deleted@example.test',
                'deleted_at': '2026-10-01T12:00:00Z',
                'status': 'disabled',
              },
            ]
          : isRecycleBin
              ? const <Map<String, dynamic>>[]
              : <Map<String, dynamic>>[
                  {
                    'id': 'employee-active',
                    'login': 'active.worker',
                    'contact_email': 'active@example.test',
                    'status': 'active',
                  },
                ];
      response = http.Response(
        jsonEncode({'items': items}),
        200,
        headers: {'content-type': 'application/json'},
      );
    } else if (request.method == 'POST' && path.endsWith('/employees/employee-deleted/restore')) {
      restoreRequests += 1;
      restoreMethod = request.method;
      restorePath = path;
      tombstoneVisible = false;
      response = http.Response(
        jsonEncode({'id': 'employee-deleted', 'restored': true}),
        200,
        headers: {'content-type': 'application/json'},
      );
    } else {
      response = http.Response('unexpected request: ${request.method} ${request.url}', 404);
    }

    return http.StreamedResponse(
      Stream<List<int>>.value(response.bodyBytes),
      response.statusCode,
      headers: response.headers,
      request: request,
    );
  }
}

Future<void> _installAuthSession() async {
  SharedPreferences.setMockInitialValues(<String, Object>{});
  final secureStore = <String, String>{};
  const channel = MethodChannel('plugins.it_nomads.com/flutter_secure_storage');
  TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
      .setMockMethodCallHandler(channel, (call) async {
    final args = (call.arguments ?? const <String, Object?>{}) as Map<Object?, Object?>;
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
