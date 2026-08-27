import 'package:flutter_test/flutter_test.dart';
import 'package:prodavan/core/auth/session_store.dart';
import 'package:prodavan/core/auth/token_session.dart';

class _MemoryStore extends SessionStore {
  StoredSession? _session;

  @override
  Future<void> clear() async => _session = null;

  @override
  Future<StoredSession?> load() async => _session;

  @override
  Future<void> save({
    required String baseUrl,
    required String bearerToken,
    String? refreshToken,
    String? idToken,
    DateTime? expiresAt,
    String? companyId,
    bool keepRefreshIfNull = false,
    bool keepIdTokenIfNull = false,
  }) async {
    _session = StoredSession(
      baseUrl: baseUrl,
      bearerToken: bearerToken,
      refreshToken:
          refreshToken ?? (keepRefreshIfNull ? _session?.refreshToken : null),
      idToken: idToken ?? (keepIdTokenIfNull ? _session?.idToken : null),
      expiresAt: expiresAt,
      companyId: companyId,
    );
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('requireAccessToken returns current token when not near expiry', () async {
    final session = TokenSession(store: _MemoryStore());
    await session.applyTokens(
      baseUrl: 'http://127.0.0.1:8000/api/v1',
      accessToken: 'access-1',
      refreshToken: 'refresh-1',
      expiresAt: DateTime.now().add(const Duration(hours: 1)),
    );
    expect(await session.requireAccessToken(), 'access-1');
  });

  test('setCompanyId keeps refresh token', () async {
    final store = _MemoryStore();
    final session = TokenSession(store: store);
    await session.applyTokens(
      baseUrl: 'http://127.0.0.1:8000/api/v1',
      accessToken: 'access-1',
      refreshToken: 'refresh-1',
      idToken: 'id-1',
      expiresAt: DateTime.now().add(const Duration(hours: 1)),
    );
    await session.setCompanyId('co_1');
    final stored = await store.load();
    expect(stored?.companyId, 'co_1');
    expect(stored?.refreshToken, 'refresh-1');
    expect(stored?.idToken, 'id-1');
  });
}
