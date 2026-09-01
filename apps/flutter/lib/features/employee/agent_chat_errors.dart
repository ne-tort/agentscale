import 'dart:convert';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/features/employee/project_chat_controller.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Localize agent SSE/API errors for snackbars (not chat bubbles).
String localizeAgentChatError(Object error, AppLocalizations l10n) =>
    presentAgentChatError(error, l10n).display;

AppErrorPresentation presentAgentChatError(Object error, AppLocalizations l10n) {
  if (error is ProdavanApiException) {
    return AppErrors.present(error, l10n);
  }
  if (error is AgentStreamError) {
    final code = error.data['code']?.toString();
    final message = error.data['message']?.toString() ?? '';
    final status = _statusForAgentCode(code);
    final body = jsonEncode({
      if (code != null && code.isNotEmpty) 'code': code,
      if (message.isNotEmpty) 'detail': message,
    });
    return AppErrors.present(ProdavanApiException(status, body), l10n);
  }
  return AppErrors.present(error, l10n);
}

int _statusForAgentCode(String? code) {
  return switch (code) {
    'POD_NOT_RUNNING' => 409,
    'BRIDGE_SEND_FAILED' => 502,
    _ => 503,
  };
}
