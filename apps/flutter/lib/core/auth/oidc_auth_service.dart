import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'package:crypto/crypto.dart';
import 'package:flutter_appauth/flutter_appauth.dart';
import 'package:http/http.dart' as http;
import 'package:url_launcher/url_launcher.dart';

/// PKCE OIDC sign-in for mobile (AppAuth) and desktop loopback (L01/L05).
class OidcAuthResult {
  const OidcAuthResult({
    required this.accessToken,
    this.refreshToken,
    this.idToken,
    this.accessTokenExpiration,
  });

  final String accessToken;
  final String? refreshToken;
  final String? idToken;
  final DateTime? accessTokenExpiration;
}

class OidcAuthService {
  const OidcAuthService();

  static const scopes = ['openid', 'profile', 'email'];
  static const _appAuth = FlutterAppAuth();

  /// Builds authorize query for KC (incl. optional [kcIdpHint] for Identity Broker).
  static Map<String, String> authorizationQuery({
    required String clientId,
    required String redirectUri,
    required String codeChallenge,
    required String state,
    String? kcIdpHint,
    List<String> scopes = scopes,
  }) {
    final query = <String, String>{
      'client_id': clientId,
      'response_type': 'code',
      'scope': scopes.join(' '),
      'redirect_uri': redirectUri,
      'code_challenge': codeChallenge,
      'code_challenge_method': 'S256',
      'state': state,
    };
    final hint = kcIdpHint?.trim();
    if (hint != null && hint.isNotEmpty) {
      query['kc_idp_hint'] = hint;
    }
    return query;
  }

  Future<OidcAuthResult> signIn(
    Map<String, dynamic> oidc, {
    String? kcIdpHint,
  }) async {
    if (Platform.isAndroid || Platform.isIOS) {
      return _signInMobile(oidc, kcIdpHint: kcIdpHint);
    }
    return _signInDesktopLoopback(oidc, kcIdpHint: kcIdpHint);
  }

  Future<OidcAuthResult?> refresh({
    required Map<String, dynamic> oidc,
    required String refreshToken,
  }) async {
    final tokenEndpoint = oidc['token_endpoint'] as String?;
    final clientId = oidc['client_id'] as String?;
    if (tokenEndpoint == null || clientId == null || refreshToken.isEmpty) {
      return null;
    }
    final res = await http.post(
      Uri.parse(tokenEndpoint),
      headers: {'Content-Type': 'application/x-www-form-urlencoded'},
      body: {
        'grant_type': 'refresh_token',
        'client_id': clientId,
        'refresh_token': refreshToken,
      },
    );
    if (res.statusCode < 200 || res.statusCode >= 300) {
      return null;
    }
    final body = jsonDecode(res.body) as Map<String, dynamic>;
    final access = body['access_token'] as String?;
    if (access == null || access.isEmpty) return null;
    return OidcAuthResult(
      accessToken: access,
      refreshToken: body['refresh_token'] as String? ?? refreshToken,
      idToken: body['id_token'] as String?,
      accessTokenExpiration: _parseExpiry(body['expires_in']),
    );
  }

  /// Best-effort Keycloak logout: revoke refresh/access, then RP-initiated end session.
  ///
  /// Local tokens must still be cleared by the caller even if remote calls fail.
  Future<void> signOut({
    required Map<String, dynamic> oidc,
    String? refreshToken,
    String? accessToken,
    String? idToken,
  }) async {
    final refresh = refreshToken?.trim();
    final access = accessToken?.trim();
    final id = idToken?.trim();

    if (refresh != null && refresh.isNotEmpty) {
      await _revokeToken(oidc: oidc, token: refresh, tokenTypeHint: 'refresh_token');
    }
    if (access != null && access.isNotEmpty) {
      await _revokeToken(oidc: oidc, token: access, tokenTypeHint: 'access_token');
    }

    try {
      if (Platform.isAndroid || Platform.isIOS) {
        await _endSessionMobile(oidc: oidc, idToken: id);
      } else {
        await _endSessionDesktop(oidc: oidc, idToken: id);
      }
    } catch (_) {
      // Browser / AppAuth end-session is best-effort; revoke already ran.
    }
  }

  Future<void> _revokeToken({
    required Map<String, dynamic> oidc,
    required String token,
    required String tokenTypeHint,
  }) async {
    final clientId = oidc['client_id'] as String?;
    final endpoint = _revocationEndpoint(oidc);
    if (clientId == null || clientId.isEmpty || endpoint == null) return;
    try {
      await http.post(
        Uri.parse(endpoint),
        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
        body: {
          'client_id': clientId,
          'token': token,
          'token_type_hint': tokenTypeHint,
        },
      );
    } catch (_) {
      // Ignore network / KC errors — local clear still proceeds.
    }
  }

  Future<void> _endSessionMobile({
    required Map<String, dynamic> oidc,
    String? idToken,
  }) async {
    final discoveryUrl = oidc['discovery_url'] as String?;
    final redirectUri = oidc['redirect_uri'] as String?;
    final id = idToken;
    if (discoveryUrl == null ||
        redirectUri == null ||
        id == null ||
        id.isEmpty) {
      // AppAuth requires id_token_hint + post_logout_redirect together.
      final logoutUri = endSessionUri(oidc: oidc, idToken: id);
      if (logoutUri != null) {
        await launchUrl(logoutUri, mode: LaunchMode.externalApplication);
      }
      return;
    }
    await _appAuth.endSession(
      EndSessionRequest(
        idTokenHint: id,
        postLogoutRedirectUrl: redirectUri,
        discoveryUrl: discoveryUrl,
      ),
    );
  }

  Future<void> _endSessionDesktop({
    required Map<String, dynamic> oidc,
    String? idToken,
  }) async {
    final logoutUri = endSessionUri(oidc: oidc, idToken: idToken);
    if (logoutUri == null) return;
    await launchUrl(logoutUri, mode: LaunchMode.externalApplication);
  }

  /// Builds Keycloak RP-initiated logout URL (public for tests).
  static Uri? endSessionUri({
    required Map<String, dynamic> oidc,
    String? idToken,
  }) {
    final endpoint = _endSessionEndpoint(oidc);
    final clientId = oidc['client_id'] as String?;
    if (endpoint == null || clientId == null || clientId.isEmpty) return null;

    final redirect = (oidc['redirect_uri_desktop'] as String?) ??
        (oidc['redirect_uri'] as String?);
    final query = <String, String>{
      'client_id': clientId,
    };
    final id = idToken?.trim();
    if (id != null && id.isNotEmpty) {
      query['id_token_hint'] = id;
      if (redirect != null && redirect.isNotEmpty) {
        query['post_logout_redirect_uri'] = redirect;
      }
    }
    return Uri.parse(endpoint).replace(queryParameters: query);
  }

  static String? _endSessionEndpoint(Map<String, dynamic> oidc) {
    final explicit = oidc['end_session_endpoint'] as String?;
    if (explicit != null && explicit.isNotEmpty) return explicit;
    final issuer = oidc['issuer'] as String?;
    if (issuer == null || issuer.isEmpty) return null;
    return '${issuer.replaceAll(RegExp(r'/+$'), '')}/protocol/openid-connect/logout';
  }

  static String? _revocationEndpoint(Map<String, dynamic> oidc) {
    final explicit = oidc['revocation_endpoint'] as String?;
    if (explicit != null && explicit.isNotEmpty) return explicit;
    final issuer = oidc['issuer'] as String?;
    if (issuer == null || issuer.isEmpty) return null;
    return '${issuer.replaceAll(RegExp(r'/+$'), '')}/protocol/openid-connect/revoke';
  }

  Future<OidcAuthResult> _signInMobile(
    Map<String, dynamic> oidc, {
    String? kcIdpHint,
  }) async {
    final clientId = oidc['client_id'] as String?;
    final redirectUri = oidc['redirect_uri'] as String?;
    final discoveryUrl = oidc['discovery_url'] as String?;
    if (clientId == null || redirectUri == null || discoveryUrl == null) {
      throw StateError('Incomplete OIDC config for AppAuth');
    }

    final hint = kcIdpHint?.trim();
    final response = await _appAuth.authorizeAndExchangeCode(
      AuthorizationTokenRequest(
        clientId,
        redirectUri,
        discoveryUrl: discoveryUrl,
        scopes: scopes,
        promptValues: ['login'],
        additionalParameters: (hint != null && hint.isNotEmpty)
            ? {'kc_idp_hint': hint}
            : null,
      ),
    );
    final access = response.accessToken;
    if (access == null || access.isEmpty) {
      throw StateError('OIDC login cancelled or failed');
    }
    return OidcAuthResult(
      accessToken: access,
      refreshToken: response.refreshToken,
      idToken: response.idToken,
      accessTokenExpiration: response.accessTokenExpirationDateTime,
    );
  }

  Future<OidcAuthResult> _signInDesktopLoopback(
    Map<String, dynamic> oidc, {
    String? kcIdpHint,
  }) async {
    final authEndpoint = oidc['authorization_endpoint'] as String?;
    final tokenEndpoint = oidc['token_endpoint'] as String?;
    final clientId = oidc['client_id'] as String?;
    final redirectUri = (oidc['redirect_uri_desktop'] as String?) ??
        (oidc['redirect_uri'] as String?);
    if (authEndpoint == null ||
        tokenEndpoint == null ||
        clientId == null ||
        redirectUri == null) {
      throw StateError('Incomplete OIDC config for desktop PKCE');
    }

    final redirect = Uri.parse(redirectUri);
    final port = redirect.port == 0 ? 8765 : redirect.port;
    final path = redirect.path.isEmpty ? '/oauth/callback' : redirect.path;
    final callbackPath = path.startsWith('/') ? path : '/$path';

    final verifier = _randomVerifier();
    final challenge = _codeChallenge(verifier);
    final state = _randomVerifier();

    final server = await HttpServer.bind(redirect.host.isEmpty ? '127.0.0.1' : redirect.host, port);
    try {
      final query = authorizationQuery(
        clientId: clientId,
        redirectUri: redirectUri,
        codeChallenge: challenge,
        state: state,
        kcIdpHint: kcIdpHint,
      );
      final authUri = Uri.parse(authEndpoint).replace(queryParameters: query);
      final launched = await launchUrl(authUri, mode: LaunchMode.externalApplication);
      if (!launched) {
        throw StateError('Could not open browser for OIDC login');
      }

      final code = await _awaitAuthCode(server, expectedState: state, callbackPath: callbackPath);
      final tokenRes = await http.post(
        Uri.parse(tokenEndpoint),
        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
        body: {
          'grant_type': 'authorization_code',
          'client_id': clientId,
          'redirect_uri': redirectUri,
          'code': code,
          'code_verifier': verifier,
        },
      );
      if (tokenRes.statusCode < 200 || tokenRes.statusCode >= 300) {
        throw StateError('Token exchange failed: ${tokenRes.statusCode} ${tokenRes.body}');
      }
      final body = jsonDecode(tokenRes.body) as Map<String, dynamic>;
      final access = body['access_token'] as String?;
      if (access == null || access.isEmpty) {
        throw StateError('Token response missing access_token');
      }
      return OidcAuthResult(
        accessToken: access,
        refreshToken: body['refresh_token'] as String?,
        idToken: body['id_token'] as String?,
        accessTokenExpiration: _parseExpiry(body['expires_in']),
      );
    } finally {
      await server.close(force: true);
    }
  }

  Future<String> _awaitAuthCode(
    HttpServer server, {
    required String expectedState,
    required String callbackPath,
  }) async {
    final completer = Completer<String>();
    server.listen((request) async {
      if (request.uri.path != callbackPath) {
        request.response
          ..statusCode = HttpStatus.notFound
          ..close();
        return;
      }
      final error = request.uri.queryParameters['error'];
      if (error != null) {
        if (!completer.isCompleted) {
          completer.completeError(StateError('OIDC error: $error'));
        }
        request.response
          ..statusCode = HttpStatus.ok
          ..headers.contentType = ContentType.html
          ..write('<html><body>Login failed. You can close this tab.</body></html>')
          ..close();
        return;
      }
      final state = request.uri.queryParameters['state'];
      final code = request.uri.queryParameters['code'];
      if (state != expectedState || code == null || code.isEmpty) {
        if (!completer.isCompleted) {
          completer.completeError(StateError('Invalid OIDC callback'));
        }
      } else if (!completer.isCompleted) {
        completer.complete(code);
      }
      request.response
        ..statusCode = HttpStatus.ok
        ..headers.contentType = ContentType.html
        ..write('<html><body>Signed in. Return to Prodavan.</body></html>')
        ..close();
    });

    return completer.future.timeout(
      const Duration(minutes: 5),
      onTimeout: () => throw StateError('OIDC login timed out'),
    );
  }

  static String _randomVerifier() {
    const chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~';
    final rand = Random.secure();
    return List.generate(64, (_) => chars[rand.nextInt(chars.length)]).join();
  }

  static String _codeChallenge(String verifier) {
    final digest = sha256.convert(utf8.encode(verifier));
    return base64Url.encode(digest.bytes).replaceAll('=', '');
  }

  static DateTime? _parseExpiry(Object? expiresIn) {
    if (expiresIn is int) {
      return DateTime.now().add(Duration(seconds: expiresIn));
    }
    if (expiresIn is String) {
      final parsed = int.tryParse(expiresIn);
      if (parsed != null) return DateTime.now().add(Duration(seconds: parsed));
    }
    return null;
  }
}

const oidcAuthService = OidcAuthService();
