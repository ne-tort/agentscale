import 'package:flutter_test/flutter_test.dart';
import 'package:prodavan/core/auth/oidc_auth_service.dart';

void main() {
  group('OidcAuthService.authorizationQuery', () {
    test('omits kc_idp_hint when null or blank', () {
      final q = OidcAuthService.authorizationQuery(
        clientId: 'prodavan-flutter',
        redirectUri: 'http://127.0.0.1:8765/callback',
        codeChallenge: 'chal',
        state: 'st',
      );
      expect(q['kc_idp_hint'], isNull);
      expect(q['client_id'], 'prodavan-flutter');
      expect(q['response_type'], 'code');
      expect(q['code_challenge_method'], 'S256');

      final blank = OidcAuthService.authorizationQuery(
        clientId: 'prodavan-flutter',
        redirectUri: 'http://127.0.0.1:8765/callback',
        codeChallenge: 'chal',
        state: 'st',
        kcIdpHint: '  ',
      );
      expect(blank.containsKey('kc_idp_hint'), isFalse);
    });

    test('sets kc_idp_hint for vk and yandex broker aliases', () {
      for (final hint in ['vk', 'yandex']) {
        final q = OidcAuthService.authorizationQuery(
          clientId: 'prodavan-flutter',
          redirectUri: 'http://127.0.0.1:8765/callback',
          codeChallenge: 'chal',
          state: 'st',
          kcIdpHint: hint,
        );
        expect(q['kc_idp_hint'], hint);
      }
    });
  });
}
