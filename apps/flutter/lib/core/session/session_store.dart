import 'package:shared_preferences/shared_preferences.dart';

/// Persists auth tokens and active workspace ids.
class SessionStore {
  SessionStore(this._prefs);

  final SharedPreferences _prefs;

  static const _tokenKey = 'access_token';
  static const _refreshKey = 'refresh_token';
  static const _cabinetKey = 'active_cabinet_id';
  static const _projectKey = 'active_project_id';

  static Future<SessionStore> open() async {
    return SessionStore(await SharedPreferences.getInstance());
  }

  String? get accessToken => _prefs.getString(_tokenKey);
  String? get refreshToken => _prefs.getString(_refreshKey);
  String? get activeCabinetId => _prefs.getString(_cabinetKey);
  String? get activeProjectId => _prefs.getString(_projectKey);

  Future<void> saveToken(String token) => _prefs.setString(_tokenKey, token);

  Future<void> saveRefreshToken(String? token) async {
    if (token == null || token.isEmpty) {
      await _prefs.remove(_refreshKey);
    } else {
      await _prefs.setString(_refreshKey, token);
    }
  }

  Future<void> saveTokens({required String accessToken, String? refreshToken}) async {
    await saveToken(accessToken);
    if (refreshToken != null) {
      await saveRefreshToken(refreshToken);
    }
  }

  Future<void> saveActiveCabinet(String? cabinetId) async {
    if (cabinetId == null) {
      await _prefs.remove(_cabinetKey);
    } else {
      await _prefs.setString(_cabinetKey, cabinetId);
    }
  }

  Future<void> saveActiveProject(String? projectId) async {
    if (projectId == null) {
      await _prefs.remove(_projectKey);
    } else {
      await _prefs.setString(_projectKey, projectId);
    }
  }

  Future<void> clear() async {
    await _prefs.remove(_tokenKey);
    await _prefs.remove(_refreshKey);
    await _prefs.remove(_cabinetKey);
    await _prefs.remove(_projectKey);
  }
}
