import 'dart:convert';

import 'package:flutter/material.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/widgets/app_snack_bar.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Parsed error: short UI text + compact diagnostic for clipboard (never HTML).
class AppErrorPresentation {
  const AppErrorPresentation({
    required this.display,
    required this.diagnostic,
  });

  final String display;
  final String diagnostic;
}

/// Centralized mapping of API / network / unexpected errors to user-facing copy.
abstract final class AppErrors {
  static AppErrorPresentation present(Object error, AppLocalizations l10n) {
    if (error is ProdavanApiException) {
      return _fromApi(error.statusCode, error.body, l10n);
    }
    if (error is FormatException) {
      final msg = error.message.trim();
      return AppErrorPresentation(
        display: msg.isNotEmpty ? msg : l10n.errorUnexpected,
        diagnostic: 'FormatException: $msg',
      );
    }
    final asText = error.toString().trim();
    if (_looksLikeHtml(asText) || _looksLikeGatewayDump(asText)) {
      final status = _statusFromLooseText(asText) ?? 502;
      return AppErrorPresentation(
        display: _statusMessage(l10n, status),
        diagnostic: 'HTTP $status',
      );
    }
    if (_looksLikeNetwork(asText)) {
      return AppErrorPresentation(
        display: l10n.errorNetwork,
        diagnostic: asText.length > 240 ? '${asText.substring(0, 240)}…' : asText,
      );
    }
    // ProdavanApiException.toString() leaked into String? error fields.
    final embedded = _tryParseEmbeddedApiException(asText);
    if (embedded != null) {
      return _fromApi(embedded.$1, embedded.$2, l10n);
    }
    if (asText.isEmpty) {
      return AppErrorPresentation(
        display: l10n.errorUnexpected,
        diagnostic: error.runtimeType.toString(),
      );
    }
    if (asText.length > 180) {
      return AppErrorPresentation(
        display: l10n.errorUnexpected,
        diagnostic: '${asText.substring(0, 240)}…',
      );
    }
    return AppErrorPresentation(display: asText, diagnostic: asText);
  }

  static String localize(BuildContext context, Object error) =>
      present(error, AppLocalizations.of(context)).display;

  static void showSnack(BuildContext context, Object error) {
    if (!context.mounted) return;
    final parsed = present(error, AppLocalizations.of(context));
    AppSnackBar.error(
      context,
      parsed.display,
      rawMessage: parsed.diagnostic,
    );
  }

  static AppErrorPresentation _fromApi(
    int statusCode,
    String body,
    AppLocalizations l10n,
  ) {
    final trimmed = body.trim();
    if (_looksLikeHtml(trimmed) || trimmed.isEmpty) {
      return AppErrorPresentation(
        display: _statusMessage(l10n, statusCode),
        diagnostic: 'HTTP $statusCode',
      );
    }

    String? code;
    String? title;
    String? detail;
    String? message;
    try {
      final decoded = jsonDecode(trimmed);
      if (decoded is Map) {
        code = decoded['code'] as String?;
        title = decoded['title'] as String?;
        detail = decoded['detail'] as String?;
        message = decoded['message'] as String?;
      }
    } catch (_) {
      // Non-JSON body (proxy text, plain string).
    }

    final byCode = _messageForCode(l10n, code);
    if (byCode != null) {
      return AppErrorPresentation(
        display: byCode,
        diagnostic: _diagnostic(statusCode, code: code, detail: detail ?? message),
      );
    }

    final human = _firstHumanReadable([detail, message, title]);
    if (human != null && !_looksTechnical(human)) {
      return AppErrorPresentation(
        display: human,
        diagnostic: _diagnostic(statusCode, code: code, detail: human),
      );
    }

    return AppErrorPresentation(
      display: _statusMessage(l10n, statusCode),
      diagnostic: _diagnostic(statusCode, code: code, detail: detail ?? message ?? title),
    );
  }

  static String? _messageForCode(AppLocalizations l10n, String? code) {
    if (code == null || code.isEmpty) return null;
    return switch (code) {
      'KEYCLOAK_ADMIN' || 'IDENTITY_PROVIDER' => l10n.errorIdentityProvider,
      'UNAUTHORIZED' || 'NOT_AUTHENTICATED' => l10n.errorUnauthorized,
      'FORBIDDEN' || 'NOT_AUTHORIZED' => l10n.errorForbidden,
      'NOT_FOUND' => l10n.errorNotFound,
      'CONFLICT' => l10n.errorConflict,
      'VALIDATION_ERROR' => l10n.errorValidation,
      'RATE_LIMITED' => l10n.errorRateLimited,
      _ => null,
    };
  }

  static String _statusMessage(AppLocalizations l10n, int status) {
    if (status == 401) return l10n.errorUnauthorized;
    if (status == 403) return l10n.errorForbidden;
    if (status == 404) return l10n.errorNotFound;
    if (status == 409) return l10n.errorConflict;
    if (status == 422) return l10n.errorValidation;
    if (status == 429) return l10n.errorRateLimited;
    if (status >= 502 && status <= 504) return l10n.errorGateway;
    if (status >= 500) return l10n.errorServer;
    return l10n.errorHttpStatus(status);
  }

  static String _diagnostic(
    int statusCode, {
    String? code,
    String? detail,
  }) {
    final parts = <String>['HTTP $statusCode'];
    if (code != null && code.isNotEmpty) parts.add(code);
    final d = (detail ?? '').trim();
    if (d.isNotEmpty && !_looksLikeHtml(d)) {
      parts.add(d.length > 160 ? '${d.substring(0, 160)}…' : d);
    }
    return parts.join(' · ');
  }

  static String? _firstHumanReadable(List<String?> candidates) {
    for (final c in candidates) {
      final t = (c ?? '').trim();
      if (t.isNotEmpty && !_looksLikeHtml(t)) return t;
    }
    return null;
  }

  static bool _looksLikeHtml(String s) {
    final lower = s.toLowerCase();
    return lower.contains('<html') ||
        lower.contains('<!doctype') ||
        lower.contains('<head>') ||
        lower.contains('<center>') ||
        lower.contains('nginx/');
  }

  static bool _looksLikeGatewayDump(String s) {
    final lower = s.toLowerCase();
    return lower.contains('502 bad gateway') ||
        lower.contains('503 service') ||
        lower.contains('504 gateway');
  }

  static bool _looksLikeNetwork(String s) {
    final lower = s.toLowerCase();
    return lower.contains('socketexception') ||
        lower.contains('clientexception') ||
        lower.contains('failed host lookup') ||
        lower.contains('connection refused') ||
        lower.contains('connection reset') ||
        lower.contains('network is unreachable') ||
        lower.contains('timed out');
  }

  static bool _looksTechnical(String s) {
    final lower = s.toLowerCase();
    return lower.startsWith('prodavanapiexception') ||
        lower.contains('traceback') ||
        lower.contains('exception:') ||
        _looksLikeHtml(s);
  }

  static (int, String)? _tryParseEmbeddedApiException(String text) {
    final match = RegExp(
      r'ProdavanApiException\((\d+)\):\s*([\s\S]*)',
    ).firstMatch(text);
    if (match == null) return null;
    final status = int.tryParse(match.group(1)!);
    if (status == null) return null;
    return (status, match.group(2) ?? '');
  }

  static int? _statusFromLooseText(String text) {
    final m = RegExp(r'\b(502|503|504|500)\b').firstMatch(text);
    if (m == null) return null;
    return int.tryParse(m.group(1)!);
  }
}
