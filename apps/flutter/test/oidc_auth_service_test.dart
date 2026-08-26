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

  group('OidcAuthService.endSessionUri', () {
    const oidc = {
      'issuer': 'http://127.0.0.1:8080/realms/prodavan',
      'client_id': 'prodavan-flutter',
      'end_session_endpoint':
          'http://127.0.0.1:8080/realms/prodavan/protocol/openid-connect/logout',
      'redirect_uri': 'prodavan://oauth/callback',
      'redirect_uri_desktop': 'http://127.0.0.1:8765/oauth/callback',
    };

    test('includes client_id and id_token_hint when present', () {
      final uri = OidcAuthService.endSessionUri(
        oidc: oidc,
        idToken: 'eyJhbGciOiJSUzI1NiJ9.payload.sig',
      );
      expect(uri, isNotNull);
      expect(uri!.path, endsWith('/protocol/openid-connect/logout'));
      expect(uri.queryParameters['client_id'], 'prodavan-flutter');
      expect(uri.queryParameters['id_token_hint'], startsWith('eyJ'));
      expect(
        uri.queryParameters['post_logout_redirect_uri'],
        'http://127.0.0.1:8765/oauth/callback',
      );
    });

    test('omits redirect without id_token_hint', () {
      final uri = OidcAuthService.endSessionUri(oidc: oidc);
      expect(uri, isNotNull);
      expect(uri!.queryParameters['client_id'], 'prodavan-flutter');
      expect(uri.queryParameters.containsKey('id_token_hint'), isFalse);
      expect(uri.queryParameters.containsKey('post_logout_redirect_uri'), isFalse);
    });
  });
}
