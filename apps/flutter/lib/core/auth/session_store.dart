import 'package:shared_preferences/shared_preferences.dart';

/// Persist dev/OIDC session locally (L01/L05 stub — not flutter_secure_storage yet).
class SessionStore {
  static const _keyBaseUrl = 'prodavan.api.base_url';
  static const _keyToken = 'prodavan.api.bearer_token';
  static const _keyCompanyId = 'prodavan.api.company_id';

  Future<void> save({
    required String baseUrl,
    required String bearerToken,
    String? companyId,
  }) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_keyBaseUrl, baseUrl);
    await prefs.setString(_keyToken, bearerToken);
    if (companyId != null) {
      await prefs.setString(_keyCompanyId, companyId);
    } else {
      await prefs.remove(_keyCompanyId);
    }
  }

  Future<StoredSession?> load() async {
    final prefs = await SharedPreferences.getInstance();
    final baseUrl = prefs.getString(_keyBaseUrl);
    final token = prefs.getString(_keyToken);
    if (baseUrl == null || baseUrl.isEmpty || token == null || token.isEmpty) {
      return null;
    }
    return StoredSession(
      baseUrl: baseUrl,
      bearerToken: token,
      companyId: prefs.getString(_keyCompanyId),
    );
  }

  Future<void> clear() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_keyBaseUrl);
    await prefs.remove(_keyToken);
    await prefs.remove(_keyCompanyId);
  }
}

class StoredSession {
  const StoredSession({
    required this.baseUrl,
    required this.bearerToken,
    this.companyId,
  });

  final String baseUrl;
  final String bearerToken;
  final String? companyId;
}

final sessionStore = SessionStore();
