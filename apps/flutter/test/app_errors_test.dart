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
