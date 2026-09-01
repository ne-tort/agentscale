import 'package:flutter_test/flutter_test.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/l10n/app_localizations.dart';

void main() {
  Future<AppLocalizations> l10nFor(WidgetTester tester, Locale locale) async {
    late AppLocalizations l10n;
    await tester.pumpWidget(
      MaterialApp(
        locale: locale,
        localizationsDelegates: const [
          AppLocalizations.delegate,
          ...GlobalMaterialLocalizations.delegates,
        ],
        supportedLocales: AppLocalizations.supportedLocales,
        home: Builder(
          builder: (context) {
            l10n = AppLocalizations.of(context);
            return const SizedBox.shrink();
          },
        ),
      ),
    );
    return l10n;
  }

  testWidgets('maps nginx HTML 502 to friendly gateway copy', (tester) async {
    final l10n = await l10nFor(tester, const Locale('ru'));
    const html = '''
<html>
<head><title>502 Bad Gateway</title></head>
<body>
<center><h1>502 Bad Gateway</h1></center>
<hr><center>nginx/1.27.5</center>
</body>
</html>
''';
    final presented = AppErrors.present(
      ProdavanApiException(502, html),
      l10n,
    );
    expect(presented.display, l10n.errorGateway);
    expect(presented.diagnostic, 'HTTP 502');
    expect(presented.diagnostic.toLowerCase().contains('html'), isFalse);
    expect(presented.display.contains('<'), isFalse);
  });

  testWidgets('prefers KEYCLOAK_ADMIN code over raw detail', (tester) async {
    final l10n = await l10nFor(tester, const Locale('ru'));
    final presented = AppErrors.present(
      ProdavanApiException(
        502,
        '{"code":"KEYCLOAK_ADMIN","title":"Keycloak password reset failed","detail":"reset-password returned 500"}',
      ),
      l10n,
    );
    expect(presented.display, l10n.errorIdentityProvider);
    expect(presented.diagnostic.contains('KEYCLOAK_ADMIN'), isTrue);
  });

  testWidgets('unmapped code with English detail uses RU status fallback', (tester) async {
    final l10n = await l10nFor(tester, const Locale('ru'));
    final presented = AppErrors.present(
      ProdavanApiException(
        409,
        '{"code":"SOME_UNKNOWN_CODE","title":"Conflict","detail":"project is paused for English UI leak"}',
      ),
      l10n,
    );
    expect(presented.display, l10n.errorConflict);
    expect(presented.display.toLowerCase().contains('paused'), isFalse);
    expect(presented.diagnostic.contains('SOME_UNKNOWN_CODE'), isTrue);
    expect(presented.diagnostic.contains('project is paused'), isTrue);
  });

  testWidgets('PROJECT_PAUSED maps to localized copy without EN leak', (tester) async {
    final l10n = await l10nFor(tester, const Locale('ru'));
    final presented = AppErrors.present(
      ProdavanApiException(
        409,
        '{"code":"PROJECT_PAUSED","title":"Project paused","detail":"project is paused"}',
      ),
      l10n,
    );
    expect(presented.display, l10n.errorProjectPaused);
    expect(presented.display, isNot(contains('project is paused')));
  });

  testWidgets('agent runtime detail avoids generic gateway copy', (tester) async {
    final l10n = await l10nFor(tester, const Locale('ru'));
    final presented = AppErrors.present(
      ProdavanApiException(
        503,
        '{"code":"HTTP_ERROR","title":"Service Unavailable","detail":"failed to push AI key lease to pod agent-runtime"}',
      ),
      l10n,
    );
    expect(presented.display, l10n.errorAgentRuntimeUnavailable);
    expect(presented.display, isNot(l10n.errorGateway));
    expect(presented.diagnostic, contains('failed to push AI key lease'));
  });

  testWidgets('recovers from stringified ProdavanApiException', (tester) async {
    final l10n = await l10nFor(tester, const Locale('ru'));
    final presented = AppErrors.present(
      'ProdavanApiException(502): <html><body>502 Bad Gateway</body></html>',
      l10n,
    );
    expect(presented.display, l10n.errorGateway);
    expect(presented.diagnostic, 'HTTP 502');
  });
}
