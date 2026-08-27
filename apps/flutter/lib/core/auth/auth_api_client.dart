import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:prodavan/core/api/prodavan_api.dart';

/// Result of Auth Service login / refresh (Prodavan API, never Keycloak).
class AuthApiResult {
  const AuthApiResult({
    required this.accessToken,
    this.refreshToken,
    this.idToken,
    this.accessTokenExpiration,
    this.sub,
    this.roles = const [],
    this.email,
    this.username,
  });

  final String accessToken;
  final String? refreshToken;
  final String? idToken;
  final DateTime? accessTokenExpiration;
  final String? sub;
  final List<String> roles;
  final String? email;
  final String? username;

  factory AuthApiResult.fromJson(Map<String, dynamic> body) {
    final access = body['access_token'] as String?;
    if (access == null || access.isEmpty) {
      throw const FormatException('Token response missing access_token');
    }
    final expiresIn = body['expires_in'];
    DateTime? exp;
    if (expiresIn is int) {
      exp = DateTime.now().add(Duration(seconds: expiresIn));
    } else if (expiresIn is num) {
      exp = DateTime.now().add(Duration(seconds: expiresIn.round()));
    }
    final rolesRaw = body['roles'];
    return AuthApiResult(
      accessToken: access,
      refreshToken: body['refresh_token'] as String?,
      idToken: body['id_token'] as String?,
      accessTokenExpiration: exp,
      sub: body['sub'] as String?,
      roles: rolesRaw is List
          ? rolesRaw.map((e) => e.toString()).toList()
          : const [],
      email: body['email'] as String?,
      username: body['username'] as String?,
    );
  }
}

/// Thin client for in-process Auth Service HTTP API.
class AuthApiClient {
  const AuthApiClient();

  static String _root(String apiBaseUrl) =>
      apiBaseUrl.replaceAll(RegExp(r'/+$'), '');

  Future<Map<String, dynamic>> fetchConfig(String apiBaseUrl) async {
    final res = await http.get(Uri.parse('${_root(apiBaseUrl)}/auth/config'));
    if (res.statusCode < 200 || res.statusCode >= 300) {
      throw ProdavanApiException(res.statusCode, res.body);
    }
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<AuthApiResult> login({
    required String apiBaseUrl,
    required String username,
    required String password,
  }) async {
    final res = await http.post(
      Uri.parse('${_root(apiBaseUrl)}/auth/login'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({
        'username': username.trim(),
        'password': password,
      }),
    );
    if (res.statusCode < 200 || res.statusCode >= 300) {
      throw ProdavanApiException(res.statusCode, res.body);
    }
    return AuthApiResult.fromJson(jsonDecode(res.body) as Map<String, dynamic>);
  }

  Future<AuthApiResult?> refresh({
    required String apiBaseUrl,
    required String refreshToken,
  }) async {
    final res = await http.post(
      Uri.parse('${_root(apiBaseUrl)}/auth/refresh'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'refresh_token': refreshToken}),
    );
    if (res.statusCode < 200 || res.statusCode >= 300) {
      return null;
    }
    return AuthApiResult.fromJson(jsonDecode(res.body) as Map<String, dynamic>);
  }

  Future<void> logout({
    required String apiBaseUrl,
    String? refreshToken,
    String? accessToken,
    String? idToken,
  }) async {
    await http.post(
      Uri.parse('${_root(apiBaseUrl)}/auth/logout'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({
        if (refreshToken != null && refreshToken.isNotEmpty)
          'refresh_token': refreshToken,
        if (accessToken != null && accessToken.isNotEmpty)
          'access_token': accessToken,
        if (idToken != null && idToken.isNotEmpty) 'id_token': idToken,
      }),
    );
  }

  /// Absolute URL for broker start (open in browser / WebView). API redirects to KC.
  static String brokerStartUrl(String apiBaseUrl, String idp, {String format = 'redirect'}) {
    final root = _root(apiBaseUrl);
    return '$root/auth/broker/${Uri.encodeComponent(idp)}/start?format=$format';
  }
}

const authApiClient = AuthApiClient();
