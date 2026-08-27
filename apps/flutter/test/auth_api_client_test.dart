import 'package:flutter_test/flutter_test.dart';
import 'package:prodavan/core/auth/auth_api_client.dart';

void main() {
  group('AuthApiClient', () {
    test('brokerStartUrl builds API path without Keycloak host', () {
      final url = AuthApiClient.brokerStartUrl(
        'http://127.0.0.1:8088/api/v1',
        'vk',
      );
      expect(url, contains('/auth/broker/vk/start'));
      expect(url, isNot(contains('8089')));
      expect(url, isNot(contains('keycloak')));
    });

    test('AuthApiResult.fromJson maps TokenPair + claims', () {
      final result = AuthApiResult.fromJson({
        'access_token': 'aaa.bbb.ccc',
        'refresh_token': 'r',
        'expires_in': 60,
        'sub': 'user-1',
        'roles': ['employee', 'employee'],
        'email': 'a@b.c',
        'username': 'u',
      });
      expect(result.accessToken, 'aaa.bbb.ccc');
      expect(result.refreshToken, 'r');
      expect(result.sub, 'user-1');
      expect(result.roles, ['employee', 'employee']);
      expect(result.accessTokenExpiration, isNotNull);
    });
  });
}
