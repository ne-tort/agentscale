import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Persist OIDC/test session — tokens in secure storage (L01/L05).
class SessionStore {
  static const _keyBaseUrl = 'prodavan.api.base_url';
  static const _keyCompanyId = 'prodavan.api.company_id';
  static const _keyExpiresAt = 'prodavan.api.access_expires_at';
  static const _secureToken = 'prodavan.secure.bearer_token';
  static const _secureRefresh = 'prodavan.secure.refresh_token';
  static const _secureIdToken = 'prodavan.secure.id_token';
  static const _legacyToken = 'prodavan.api.bearer_token';

  static const _secure = FlutterSecureStorage(
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
  );

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
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_keyBaseUrl, baseUrl);
    await _secure.write(key: _secureToken, value: bearerToken);

    if (refreshToken != null && refreshToken.isNotEmpty) {
      await _secure.write(key: _secureRefresh, value: refreshToken);
    } else if (!keepRefreshIfNull) {
      await _secure.delete(key: _secureRefresh);
    }

    if (idToken != null && idToken.isNotEmpty) {
      await _secure.write(key: _secureIdToken, value: idToken);
    } else if (!keepIdTokenIfNull) {
      await _secure.delete(key: _secureIdToken);
    }

    if (expiresAt != null) {
      await prefs.setString(_keyExpiresAt, expiresAt.toUtc().toIso8601String());
    } else {
      await prefs.remove(_keyExpiresAt);
    }

    if (companyId != null) {
      await prefs.setString(_keyCompanyId, companyId);
    } else {
      await prefs.remove(_keyCompanyId);
    }
    await prefs.remove(_legacyToken);
  }

  Future<StoredSession?> load() async {
    final prefs = await SharedPreferences.getInstance();
    final baseUrl = prefs.getString(_keyBaseUrl);
    if (baseUrl == null || baseUrl.isEmpty) return null;

    var token = await _secure.read(key: _secureToken);
    token ??= prefs.getString(_legacyToken);
    if (token == null || token.isEmpty) return null;

    if (prefs.containsKey(_legacyToken)) {
      await _secure.write(key: _secureToken, value: token);
      await prefs.remove(_legacyToken);
    }

    DateTime? expiresAt;
    final expRaw = prefs.getString(_keyExpiresAt);
    if (expRaw != null && expRaw.isNotEmpty) {
      expiresAt = DateTime.tryParse(expRaw)?.toLocal();
    }

    return StoredSession(
      baseUrl: baseUrl,
      bearerToken: token,
      refreshToken: await _secure.read(key: _secureRefresh),
      idToken: await _secure.read(key: _secureIdToken),
      expiresAt: expiresAt,
      companyId: prefs.getString(_keyCompanyId),
    );
  }

  Future<void> clear() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_keyBaseUrl);
    await prefs.remove(_keyCompanyId);
    await prefs.remove(_keyExpiresAt);
    await prefs.remove(_legacyToken);
    await _secure.delete(key: _secureToken);
    await _secure.delete(key: _secureRefresh);
    await _secure.delete(key: _secureIdToken);
  }
}

class StoredSession {
  const StoredSession({
    required this.baseUrl,
    required this.bearerToken,
    this.refreshToken,
    this.idToken,
    this.expiresAt,
    this.companyId,
  });

  final String baseUrl;
  final String bearerToken;
  final String? refreshToken;
  final String? idToken;
  final DateTime? expiresAt;
  final String? companyId;
}

final sessionStore = SessionStore();
