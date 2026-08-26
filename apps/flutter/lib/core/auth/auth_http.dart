import 'package:http/http.dart' as http;

import 'package:prodavan/core/auth/token_session.dart';

/// Shared authorized HTTP: attaches Bearer, refreshes once on 401, retries.
abstract final class AuthHttp {
  static Future<Map<String, String>> headers([
    Map<String, String>? extra,
  ]) async {
    final token = await tokenSession.requireAccessToken();
    return {
      'Authorization': 'Bearer $token',
      'Content-Type': 'application/json',
      ...?extra,
    };
  }

  static Future<http.Response> send(
    Future<http.Response> Function(Map<String, String> headers) run, {
    Map<String, String>? extraHeaders,
  }) async {
    Future<http.Response> once() async {
      final h = await headers(extraHeaders);
      return run(h);
    }

    var res = await once();
    if (res.statusCode != 401) return res;
    final refreshed = await tokenSession.refresh(force: true);
    if (!refreshed) return res;
    return once();
  }

  static Future<http.Response> get(
    Uri uri, {
    Map<String, String>? extraHeaders,
  }) =>
      send((h) => http.get(uri, headers: h), extraHeaders: extraHeaders);

  static Future<http.Response> post(
    Uri uri, {
    Object? body,
    Map<String, String>? extraHeaders,
  }) =>
      send(
        (h) => http.post(uri, headers: h, body: body),
        extraHeaders: extraHeaders,
      );

  static Future<http.Response> put(
    Uri uri, {
    Object? body,
    Map<String, String>? extraHeaders,
  }) =>
      send(
        (h) => http.put(uri, headers: h, body: body),
        extraHeaders: extraHeaders,
      );

  static Future<http.Response> patch(
    Uri uri, {
    Object? body,
    Map<String, String>? extraHeaders,
  }) =>
      send(
        (h) => http.patch(uri, headers: h, body: body),
        extraHeaders: extraHeaders,
      );

  static Future<http.Response> delete(
    Uri uri, {
    Object? body,
    Map<String, String>? extraHeaders,
  }) =>
      send(
        (h) => http.delete(uri, headers: h, body: body),
        extraHeaders: extraHeaders,
      );
}
