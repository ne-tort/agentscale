import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/auth/auth_session_policy.dart';
import 'package:prodavan/core/auth/token_session.dart';

void main() {
  test('isTransientFailure for network and gateway errors', () {
    expect(
      isTransientFailure(
        ProdavanApiException(502, '{"code":"IDENTITY_PROVIDER"}'),
      ),
      isTrue,
    );
    expect(
      isTransientFailure(
        ProdavanApiException(503, '{"code":"AUTH_MISCONFIGURED"}'),
      ),
      isTrue,
    );
    expect(
      isTransientFailure(Exception('SocketException: Connection refused')),
      isTrue,
    );
  });

  test('isAuthRejection for domain 401 codes', () {
    expect(
      isAuthRejection(
        ProdavanApiException(401, '{"code":"UNAUTHORIZED","detail":"expired"}'),
      ),
      isTrue,
    );
    expect(
      isAuthRejection(
        ProdavanApiException(
          401,
          '{"code":"INVALID_CREDENTIALS","detail":"invalid_grant"}',
        ),
      ),
      isTrue,
    );
    expect(
      isAuthRejection(StateError('Session expired')),
      isTrue,
    );
  });

  test('shouldClearSession false for transient failures', () {
    final session = AuthSession(
      baseUrl: 'http://127.0.0.1:8000/api/v1',
      accessToken: 'tok',
      refreshToken: 'ref',
      expiresAt: DateTime.now().add(const Duration(hours: 1)),
    );
    expect(
      shouldClearSession(
        error: ProdavanApiException(502, '{"code":"IDENTITY_PROVIDER"}'),
        session: session,
        refreshAttempted: true,
      ),
      isFalse,
    );
    expect(
      shouldClearSession(
        error: Exception('SocketException: failed host lookup'),
        session: session,
        refreshAttempted: false,
      ),
      isFalse,
    );
  });

  test('shouldClearSession true after auth rejection with refresh attempted', () {
    final session = AuthSession(
      baseUrl: 'http://127.0.0.1:8000/api/v1',
      accessToken: 'tok',
      refreshToken: 'ref',
      expiresAt: DateTime.now().subtract(const Duration(minutes: 5)),
    );
    expect(
      shouldClearSession(
        error: ProdavanApiException(401, '{"code":"UNAUTHORIZED"}'),
        session: session,
        refreshAttempted: true,
      ),
      isTrue,
    );
  });

  test('shouldClearSession false for 403 forbidden', () {
    final session = AuthSession(
      baseUrl: 'http://127.0.0.1:8000/api/v1',
      accessToken: 'tok',
      refreshToken: 'ref',
    );
    expect(
      shouldClearSession(
        error: ProdavanApiException(403, '{"code":"FORBIDDEN"}'),
        session: session,
        refreshAttempted: false,
      ),
      isFalse,
    );
  });

  test('shouldAttemptRefresh on 401 and hard expiry', () {
    final session = AuthSession(
      baseUrl: 'http://127.0.0.1:8000/api/v1',
      accessToken: 'tok',
      refreshToken: 'ref',
      expiresAt: DateTime.now().subtract(const Duration(minutes: 1)),
    );
    expect(
      shouldAttemptRefresh(
        ProdavanApiException(401, '{"code":"UNAUTHORIZED"}'),
        session: session,
      ),
      isTrue,
    );
    expect(
      shouldAttemptRefresh(
        ProdavanApiException(502, '{"code":"IDENTITY_PROVIDER"}'),
        session: session,
      ),
      isFalse,
    );
  });
}
