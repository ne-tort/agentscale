import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';

import 'package:prodavan/core/auth/auth_config.dart';
import 'package:prodavan/core/auth/oidc_auth_service.dart';
import 'package:prodavan/core/auth/session_store.dart';
import 'package:prodavan/core/config/api_base.dart';
import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/session/work_context.dart';

/// In-memory + persisted auth session (access / refresh / id + expiry).
@immutable
class AuthSession {
  const AuthSession({
    required this.baseUrl,
    required this.accessToken,
    this.refreshToken,
    this.idToken,
    this.expiresAt,
    this.companyId,
  });

  final String baseUrl;
  final String accessToken;
  final String? refreshToken;
  final String? idToken;
  final DateTime? expiresAt;
  final String? companyId;

  AuthSession copyWith({
    String? baseUrl,
    String? accessToken,
    String? refreshToken,
    String? idToken,
    DateTime? expiresAt,
    String? companyId,
    bool clearCompanyId = false,
  }) {
    return AuthSession(
      baseUrl: baseUrl ?? this.baseUrl,
      accessToken: accessToken ?? this.accessToken,
      refreshToken: refreshToken ?? this.refreshToken,
      idToken: idToken ?? this.idToken,
      expiresAt: expiresAt ?? this.expiresAt,
      companyId: clearCompanyId ? null : (companyId ?? this.companyId),
    );
  }
}

/// Single owner of JWT lifecycle: load, password/OIDC login, proactive refresh.
///
/// API clients call [requireAccessToken] (skew-aware). On 401 use [refresh]
/// once via [AuthHttp].
class TokenSession extends ChangeNotifier {
  TokenSession({
    SessionStore? store,
    OidcAuthService? oidc,
  })  : _store = store ?? sessionStore,
        _oidc = oidc ?? oidcAuthService;

  static const refreshSkew = Duration(seconds: 60);

  final SessionStore _store;
  final OidcAuthService _oidc;

  AuthSession? _session;
  Map<String, dynamic>? _oidcConfig;
  String? _authMode;
  Future<bool>? _refreshInflight;

  AuthSession? get session => _session;
  bool get isAuthenticated => (_session?.accessToken.isNotEmpty) ?? false;
  String get baseUrl => _session?.baseUrl ?? ApiBase.value;
  String? get accessTokenOrNull => _session?.accessToken;
  String? get companyId => _session?.companyId;

  /// Restore from secure storage (cold start). Does not hit network.
  Future<AuthSession?> restore() async {
    final stored = await _store.load();
    if (stored == null) {
      _session = null;
      _syncContexts();
      notifyListeners();
      return null;
    }
    _session = AuthSession(
      baseUrl: stored.baseUrl,
      accessToken: stored.bearerToken,
      refreshToken: stored.refreshToken,
      idToken: stored.idToken,
      expiresAt: stored.expiresAt,
      companyId: stored.companyId,
    );
    _syncContexts();
    notifyListeners();
    return _session;
  }

  /// Access token valid for API calls; refreshes when near expiry.
  Future<String> requireAccessToken({bool forceRefresh = false}) async {
    final current = _session;
    if (current == null || current.accessToken.isEmpty) {
      throw StateError('Not authenticated');
    }
    final needs = forceRefresh || _needsRefresh(current);
    if (needs) {
      final hasRefresh =
          current.refreshToken != null && current.refreshToken!.isNotEmpty;
      if (hasRefresh) {
        final ok = await refresh(force: true);
        if (!ok && _isHardExpired(_session ?? current)) {
          throw StateError('Session expired');
        }
      } else if (_isHardExpired(current)) {
        throw StateError('Session expired');
      }
    }
    final next = _session;
    if (next == null || next.accessToken.isEmpty) {
      throw StateError('Not authenticated');
    }
    return next.accessToken;
  }

  bool _needsRefresh(AuthSession s) {
    final exp = s.expiresAt;
    if (exp == null) return false;
    return DateTime.now().isAfter(exp.subtract(refreshSkew));
  }

  bool _isHardExpired(AuthSession s) {
    final exp = s.expiresAt;
    if (exp == null) return false;
    return DateTime.now().isAfter(exp);
  }

  /// Apply tokens from OIDC / password / test login.
  Future<void> applyTokens({
    required String baseUrl,
    required String accessToken,
    String? refreshToken,
    String? idToken,
    DateTime? expiresAt,
    int? expiresInSeconds,
    String? companyId,
    bool keepCompanyId = false,
  }) async {
    final exp = expiresAt ??
        (expiresInSeconds != null
            ? DateTime.now().add(Duration(seconds: expiresInSeconds))
            : _expFromJwt(accessToken));
    final prevCompany = keepCompanyId ? _session?.companyId : null;
    final next = AuthSession(
      baseUrl: baseUrl,
      accessToken: accessToken,
      refreshToken: refreshToken,
      idToken: idToken,
      expiresAt: exp,
      companyId: companyId ?? prevCompany,
    );
    await _persist(next);
  }

  Future<void> setCompanyId(String? companyId) async {
    final current = _session;
    if (current == null) return;
    final next = current.copyWith(
      companyId: companyId,
      clearCompanyId: companyId == null,
    );
    await _persist(next, keepRefreshIfNull: true, keepIdTokenIfNull: true);
  }

  /// Resource-owner password grant (company_id or username + password).
  Future<AuthSession> loginWithPassword({
    required String baseUrl,
    required String username,
    required String password,
  }) async {
    final oidc = await _ensureOidc(baseUrl);
    final result = await _oidc.loginWithPassword(
      oidc: oidc,
      username: username,
      password: password,
    );
    await applyTokens(
      baseUrl: baseUrl,
      accessToken: result.accessToken,
      refreshToken: result.refreshToken,
      idToken: result.idToken,
      expiresAt: result.accessTokenExpiration,
    );
    return _session!;
  }

  Future<bool> refresh({bool force = false}) {
    final inflight = _refreshInflight;
    if (inflight != null) return inflight;
    final future = _refreshBody(force: force);
    _refreshInflight = future;
    return future.whenComplete(() {
      if (identical(_refreshInflight, future)) {
        _refreshInflight = null;
      }
    });
  }

  Future<bool> _refreshBody({required bool force}) async {
    final current = _session;
    if (current == null) return false;
    if (!force && !_needsRefresh(current)) return true;
    final refreshTok = current.refreshToken;
    if (refreshTok == null || refreshTok.isEmpty) return false;

    try {
      final oidc = await _ensureOidc(current.baseUrl);
      final result = await _oidc.refresh(
        oidc: oidc,
        refreshToken: refreshTok,
      );
      if (result == null) return false;
      await applyTokens(
        baseUrl: current.baseUrl,
        accessToken: result.accessToken,
        refreshToken: result.refreshToken ?? refreshTok,
        idToken: result.idToken ?? current.idToken,
        expiresAt: result.accessTokenExpiration,
        companyId: current.companyId,
      );
      return true;
    } catch (_) {
      return false;
    }
  }

  Future<void> clear() async {
    _session = null;
    _oidcConfig = null;
    _authMode = null;
    await _store.clear();
    workContext.clear();
    adminContext.clear();
    companyContext.clear();
    notifyListeners();
  }

  Future<Map<String, dynamic>> _ensureOidc(String baseUrl) async {
    if (_oidcConfig != null && _authMode == 'oidc') {
      return _oidcConfig!;
    }
    final cfg = await AuthConfigClient(baseUrl: baseUrl).fetch();
    _authMode = (cfg['auth_mode'] as String?)?.trim().toLowerCase();
    final oidc = cfg['oidc'];
    if (_authMode != 'oidc' || oidc is! Map<String, dynamic>) {
      throw StateError('OIDC auth is not configured on $baseUrl');
    }
    _oidcConfig = oidc;
    return oidc;
  }

  Future<void> _persist(
    AuthSession next, {
    bool keepRefreshIfNull = false,
    bool keepIdTokenIfNull = false,
  }) async {
    await _store.save(
      baseUrl: next.baseUrl,
      bearerToken: next.accessToken,
      refreshToken: next.refreshToken,
      idToken: next.idToken,
      expiresAt: next.expiresAt,
      companyId: next.companyId,
      keepRefreshIfNull: keepRefreshIfNull,
      keepIdTokenIfNull: keepIdTokenIfNull,
    );
    _session = next;
    _syncContexts();
    notifyListeners();
  }

  void _syncContexts() {
    final s = _session;
    if (s == null) {
      workContext.clear();
      adminContext.clear();
      companyContext.clear();
      return;
    }
    workContext.setSession(baseUrl: s.baseUrl, bearerToken: s.accessToken);
    workContext.companyId = s.companyId;
    adminContext.setSession(baseUrl: s.baseUrl, bearerToken: s.accessToken);
    companyContext.setSession(baseUrl: s.baseUrl, bearerToken: s.accessToken);
    if (s.companyId != null) {
      companyContext.companyId ??= s.companyId;
    }
  }

  static DateTime? _expFromJwt(String token) {
    try {
      final parts = token.split('.');
      if (parts.length < 2) return null;
      final payload = parts[1];
      final normalized = base64Url.normalize(payload);
      final map = jsonDecode(utf8.decode(base64Url.decode(normalized)));
      if (map is! Map) return null;
      final exp = map['exp'];
      if (exp is int) {
        return DateTime.fromMillisecondsSinceEpoch(exp * 1000, isUtc: true)
            .toLocal();
      }
      if (exp is num) {
        return DateTime.fromMillisecondsSinceEpoch(
          (exp * 1000).round(),
          isUtc: true,
        ).toLocal();
      }
    } catch (_) {}
    return null;
  }
}

final tokenSession = TokenSession();
