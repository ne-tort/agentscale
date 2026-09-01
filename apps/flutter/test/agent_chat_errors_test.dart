import 'package:flutter_test/flutter_test.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';

import 'package:prodavan/core/api/agent_stream_error.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/features/employee/agent_chat_errors.dart';
import 'package:prodavan/l10n/app_localizations.dart';

void main() {
  Future<AppLocalizations> l10nFor(WidgetTester tester) async {
    late AppLocalizations l10n;
    await tester.pumpWidget(
      MaterialApp(
        locale: const Locale('ru'),
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

  testWidgets('AGENT_CREDENTIAL_MISSING maps to localized copy, not gateway', (tester) async {
    final l10n = await l10nFor(tester);
    final presented = presentAgentChatError(
      const AgentStreamError({
        'code': 'AGENT_CREDENTIAL_MISSING',
        'message': 'cursor_sdk: API key not available in pod runtime',
      }),
      l10n,
    );
    expect(presented.display, l10n.errorAgentCredentialMissing);
    expect(presented.display, isNot(l10n.errorGateway));
    expect(presented.diagnostic, contains('AGENT_CREDENTIAL_MISSING'));
  });

  testWidgets('AGENT_STUB_RESPONSE maps to localized copy, not gateway', (tester) async {
    final l10n = await l10nFor(tester);
    final presented = presentAgentChatError(
      const AgentStreamError({
        'code': 'AGENT_STUB_RESPONSE',
        'message': 'agent runtime returned a stub response',
      }),
      l10n,
    );
    expect(presented.display, l10n.errorAgentStubResponse);
    expect(presented.display, isNot(l10n.errorGateway));
  });

  testWidgets('AppErrors.present maps AgentStreamError without minified leak', (tester) async {
    final l10n = await l10nFor(tester);
    final presented = AppErrors.present(
      const AgentStreamError({
        'code': 'BRIDGE_UNREACHABLE',
        'message': 'Connection refused',
      }),
      l10n,
    );
    expect(presented.display, l10n.errorAgentBridge);
    expect(presented.display, isNot(l10n.errorUnexpected));
    expect(presented.diagnostic, contains('BRIDGE_UNREACHABLE'));
    expect(presented.diagnostic.toLowerCase(), isNot(contains('minified')));
  });

  testWidgets('AGENT_PROVIDER_NETWORK maps to localized network copy', (tester) async {
    final l10n = await l10nFor(tester);
    final presented = presentAgentChatError(
      const AgentStreamError({
        'code': 'AGENT_PROVIDER_NETWORK',
        'message': 'Network request failed',
      }),
      l10n,
    );
    expect(presented.display, l10n.errorAgentProviderNetwork);
    expect(presented.diagnostic, contains('Network request failed'));
  });

  testWidgets('AGENT_INVALID_API_KEY maps to localized invalid key copy', (tester) async {
    final l10n = await l10nFor(tester);
    final presented = presentAgentChatError(
      const AgentStreamError({
        'code': 'AGENT_INVALID_API_KEY',
        'message': 'Invalid User API Key',
      }),
      l10n,
    );
    expect(presented.display, l10n.errorAgentInvalidApiKey);
    expect(presented.display, isNot(l10n.errorGateway));
    expect(presented.diagnostic, contains('Invalid User API Key'));
  });
}
