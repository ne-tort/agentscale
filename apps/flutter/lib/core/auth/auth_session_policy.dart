import 'dart:convert';
import 'dart:io';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/auth/token_session.dart';

/// Domain auth error codes returned by Prodavan API (never raw Keycloak).
const authRejectionCodes = {
  'UNAUTHORIZED',
  'NOT_AUTHENTICATED',
  'INVALID_CREDENTIALS',
  'SESSION_CLOSED',
};

/// Failures where the session should be kept (server/IdP temporarily unavailable).
const transientAuthCodes = {
  'IDENTITY_PROVIDER',
  'AUTH_MISCONFIGURED',
  'RATE_LIMITED',
};

String? apiErrorCode(Object error) {
  if (error is! ProdavanApiException) return null;
  final body = error.body.trim();
  if (body.isEmpty) return null;
  try {
    final decoded = jsonDecode(body);
    if (decoded is Map) {
      final code = decoded['code'];
      if (code is String && code.isNotEmpty) return code;
    }
  } catch (_) {}
  return null;
}

bool isTransientFailure(Object error) {
  if (error is ProdavanApiException) {
    final code = apiErrorCode(error);
    if (code != null && transientAuthCodes.contains(code)) return true;
    final status = error.statusCode;
    if (status >= 502 && status <= 504) return true;
    if (status == 503) return true;
    if (status == 401 && code == null) {
      // HTML/proxy 401 without domain code — treat as transient during restore.
      final body = error.body.trim().toLowerCase();
      if (body.contains('<html') || body.isEmpty) return true;
    }
    return false;
  }
  if (error is StateError) {
    final msg = error.message;
    if (msg == 'Session expired' || msg == 'Not authenticated') return false;
  }
  return _looksLikeNetwork(error);
}

bool isAuthRejection(Object error) {
  if (error is StateError) {
    final msg = error.message;
    return msg == 'Session expired' || msg == 'Not authenticated';
  }
  if (error is! ProdavanApiException) return false;
  if (error.statusCode != 401) return false;
  final code = apiErrorCode(error);
  if (code != null) return authRejectionCodes.contains(code);
  // Plain 401 JSON problem without code still means re-login.
  final body = error.body.trim();
  if (body.isEmpty) return false;
  if (body.toLowerCase().contains('<html')) return false;
  return true;
}

bool isHardExpired(AuthSession? session) {
  if (session == null) return true;
  final exp = session.expiresAt;
  if (exp == null) return false;
  return DateTime.now().isAfter(exp);
}

bool shouldAttemptRefresh(Object error, {required AuthSession? session}) {
  if (isTransientFailure(error)) return false;
  if (isAuthRejection(error)) return true;
  if (error is ProdavanApiException && error.statusCode == 401) return true;
  if (isHardExpired(session)) return true;
  return false;
}

bool shouldClearSession({
  required Object error,
  required AuthSession? session,
  required bool refreshAttempted,
}) {
  if (isTransientFailure(error)) return false;

  final hasRefresh =
      session?.refreshToken != null && session!.refreshToken!.isNotEmpty;

  if (isAuthRejection(error)) {
    if (!refreshAttempted && hasRefresh) return false;
    return true;
  }

  if (error is ProdavanApiException && error.statusCode == 403) {
    return false;
  }

  if (isHardExpired(session) && !hasRefresh) return true;

  if (refreshAttempted && isHardExpired(session) && !hasRefresh) return true;

  return false;
}

bool _looksLikeNetwork(Object error) {
  if (error is SocketException) return true;
  if (error is HttpException) return true;
  if (error is IOException) return true;
  final text = error.toString().toLowerCase();
  return text.contains('socketexception') ||
      text.contains('clientexception') ||
      text.contains('failed host lookup') ||
      text.contains('connection refused') ||
      text.contains('connection reset') ||
      text.contains('network is unreachable') ||
      text.contains('timed out');
}
