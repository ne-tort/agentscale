import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;

import 'package:prodavan/core/api/agent_stream_error.dart';
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
///
/// Rule: [display] is always l10n (locale of UI). Raw API English goes only to
/// [diagnostic] (clipboard). Never show problem+json title/detail as UI copy.
abstract final class AppErrors {
  static AppErrorPresentation present(Object error, AppLocalizations l10n) {
    if (error is ProdavanApiException) {
      return _fromApi(error.statusCode, error.body, l10n);
    }
    if (error is AgentStreamError) {
      return _fromAgentStreamError(error, l10n);
    }
    if (error is http.ClientException) {
      return AppErrorPresentation(
        display: l10n.errorAgentBridge,
        diagnostic: error.message,
      );
    }
    if (error is FormatException) {
      return AppErrorPresentation(
        display: l10n.errorUnexpected,
        diagnostic: 'FormatException: ${error.message.trim()}',
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
    final embedded = _tryParseEmbeddedApiException(asText);
    if (embedded != null) {
      return _fromApi(embedded.$1, embedded.$2, l10n);
    }
    return AppErrorPresentation(
      display: l10n.errorUnexpected,
      diagnostic: _unknownDiagnostic(error, asText),
    );
  }

  static String _unknownDiagnostic(Object error, String asText) {
    if (_looksLikeMinifiedInstance(asText)) {
      return '${error.runtimeType}';
    }
    if (asText.isEmpty) return error.runtimeType.toString();
    return asText.length > 240 ? '${asText.substring(0, 240)}…' : asText;
  }

  static bool _looksLikeMinifiedInstance(String text) {
    return text.startsWith("Instance of '") &&
        (text.contains('minified:') || text.contains('AgentStreamError'));
  }

  static AppErrorPresentation _fromAgentStreamError(
    AgentStreamError error,
    AppLocalizations l10n,
  ) {
    final code = error.code;
    final message = error.message ?? '';
    final status = switch (code) {
      'POD_NOT_RUNNING' => 409,
      'BRIDGE_SEND_FAILED' => 502,
      _ => 503,
    };
    final body = jsonEncode({
      if (code != null && code.isNotEmpty) 'code': code,
      if (message.isNotEmpty) 'detail': message,
    });
    return _fromApi(status, body, l10n);
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
    String? oauthError;
    String? oauthDescription;
    try {
      final decoded = jsonDecode(trimmed);
      if (decoded is Map) {
        code = decoded['code'] as String?;
        title = decoded['title'] as String?;
        detail = decoded['detail'] as String?;
        message = decoded['message'] as String?;
        oauthError = decoded['error'] as String?;
        oauthDescription = decoded['error_description'] as String?;
      }
    } catch (_) {
      // Non-JSON body (proxy text, plain string).
    }

    final display = _messageForCode(l10n, code) ??
        _agentRuntimeFallback(l10n, statusCode, detail ?? message) ??
        _statusMessage(l10n, statusCode);
    return AppErrorPresentation(
      display: display,
      diagnostic: _diagnostic(
        statusCode,
        code: code ?? oauthError,
        detail: detail ?? message ?? oauthDescription ?? title ?? trimmed,
      ),
    );
  }

  static String? _agentRuntimeFallback(
    AppLocalizations l10n,
    int statusCode,
    String? detail,
  ) {
    final hint = (detail ?? '').trim();
    if (hint.isEmpty) return null;
    if (!_looksLikeAgentRuntimeDetail(hint)) return null;
    if (statusCode == 409) return l10n.errorPodNotRunning;
    if (statusCode == 502) return l10n.errorAgentBridge;
    if (statusCode >= 500) return l10n.errorAgentRuntimeUnavailable;
    return null;
  }

  static bool _looksLikeAgentRuntimeDetail(String hint) {
    final lower = hint.toLowerCase();
    return lower.contains('agent') ||
        lower.contains('bridge') ||
        lower.contains('credential') ||
        lower.contains('cursor_sdk') ||
        lower.contains('openclaw') ||
        lower.contains('pod agent-runtime') ||
        lower.contains('pod runtime') ||
        lower.contains('lease') ||
        lower.contains('network request failed');
  }

  static String? _messageForCode(AppLocalizations l10n, String? code) {
    if (code == null || code.isEmpty) return null;
    return switch (code) {
      'KEYCLOAK_ADMIN' || 'IDENTITY_PROVIDER' => l10n.errorIdentityProvider,
      'INVALID_CREDENTIALS' => l10n.errorInvalidCredentials,
      'UNAUTHORIZED' || 'NOT_AUTHENTICATED' => l10n.errorUnauthorized,
      'FORBIDDEN' || 'NOT_AUTHORIZED' => l10n.errorForbidden,
      'NOT_FOUND' => l10n.errorNotFound,
      'CONFLICT' || 'PROJECT_EXISTS' => l10n.errorConflict,
      'VALIDATION_ERROR' => l10n.errorValidation,
      'RATE_LIMITED' => l10n.errorRateLimited,
      'PROJECT_PAUSED' || 'ENTITY_PAUSED' => l10n.errorProjectPaused,
      'CABINET_ARCHIVED' || 'CABINET_NOT_ARCHIVED' || 'CABINET_NOT_SOFT_DELETED' =>
        l10n.errorCabinetArchived,
      'COMPANY_SUSPENDED' || 'SUBSCRIPTION_EXPIRED' => l10n.errorCompanySuspended,
      'NO_AI_KEY' => l10n.errorNoAiKey,
      'SESSION_CLOSED' => l10n.errorSessionClosed,
      'AGENT_BUDGET' || 'AGENT_BUDGET_EXCEEDED' => l10n.errorAgentBudget,
      'POD_NOT_RUNNING' => l10n.errorPodNotRunning,
      'AGENT_RUNTIME_UNAVAILABLE' || 'AGENT_ADAPTER_DISABLED' => l10n.errorAgentRuntimeUnavailable,
      'AGENT_STUB_RESPONSE' => l10n.errorAgentStubResponse,
      'AGENT_CREDENTIAL_MISSING' => l10n.errorAgentCredentialMissing,
      'AGENT_INVALID_API_KEY' => l10n.errorAgentInvalidApiKey,
      'AGENT_PROVIDER_NETWORK' => l10n.errorAgentProviderNetwork,
      'AGENT_PROVIDER_RATE_LIMIT' => l10n.errorAgentProviderRateLimit,
      'AGENT_PROVIDER_UNAVAILABLE' => l10n.errorAgentProviderUnavailable,
      'AGENT_RUNTIME_ERROR' => l10n.errorAgentRuntimeError,
      'AGENT_ERROR' || 'AGENT_PROVIDER_ERROR' => l10n.errorAgentProvider,
      'BRIDGE_SEND_FAILED' || 'BRIDGE_EMPTY_STREAM' || 'BRIDGE_UNREACHABLE' => l10n.errorAgentBridge,
      'CASCADE_INCOMPLETE' => l10n.errorCascadeIncomplete,
      'SCHEMA_DROP_FAILED' => l10n.errorServer,
      'AUTH_MISCONFIGURED' => l10n.errorIdentityProvider,
      'ATTACHMENT_TOO_LARGE' ||
      'ATTACHMENT_FORBIDDEN' ||
      'ATTACHMENT_TYPE' =>
        l10n.errorValidation,
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
      parts.add(d.length > 800 ? '${d.substring(0, 800)}…' : d);
    }
    return parts.join(' · ');
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
