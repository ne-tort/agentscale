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
    this.accessTokenExpiration,
  });

  final String accessToken;
  final String? refreshToken;
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
      accessTokenExpiration: _parseExpiry(body['expires_in']),
    );
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
