import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/containers/project_container_poll.dart';
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

  testWidgets('agent runtime unavailable maps by code not message', (tester) async {
    final l10n = await l10nFor(tester, const Locale('ru'));
    final presented = AppErrors.present(
      ProdavanApiException(
        503,
        '{"code":"AGENT_RUNTIME_UNAVAILABLE","title":"Service Unavailable","detail":"failed to push AI key lease to pod agent-runtime"}',
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

  testWidgets('ContainerObservedFailureException shows the honest reason', (tester) async {
    final l10n = await l10nFor(tester, const Locale('ru'));
    final presented = AppErrors.present(
      ContainerObservedFailureException('ImagePullBackOff'),
      l10n,
    );
    expect(presented.display, l10n.projectAgentFailedToStart('ImagePullBackOff'));
    expect(presented.diagnostic, 'ImagePullBackOff');
  });

  testWidgets('TimeoutException with localized message keeps it', (tester) async {
    final l10n = await l10nFor(tester, const Locale('ru'));
    final presented = AppErrors.present(
      TimeoutException(l10n.containerPollTimeout, const Duration(minutes: 5)),
      l10n,
    );
    expect(presented.display, l10n.containerPollTimeout);
  });

  testWidgets('TimeoutException without message falls back to containerPollTimeout', (tester) async {
    final l10n = await l10nFor(tester, const Locale('ru'));
    final presented = AppErrors.present(TimeoutException(null), l10n);
    expect(presented.display, l10n.containerPollTimeout);
    expect(presented.diagnostic, 'TimeoutException');
  });

  testWidgets('raw String failure no longer masks as container reason', (tester) async {
    // Guard for the audit fix: a plain String must NOT reach the presenter
    // from wake flows anymore (call-sites pass ContainerObservedFailureException);
    // if one still does, it renders the generic copy — not the raw text.
    final l10n = await l10nFor(tester, const Locale('ru'));
    final presented = AppErrors.present('some raw failure', l10n);
    expect(presented.display, l10n.errorUnexpected);
  });
}
