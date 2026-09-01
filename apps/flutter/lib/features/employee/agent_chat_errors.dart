import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;

import 'package:prodavan/core/api/agent_stream_error.dart';
import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_snack_bar.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Localize agent SSE/API errors for snackbars (not chat bubbles).
String localizeAgentChatError(Object error, AppLocalizations l10n) =>
    presentAgentChatError(error, l10n).display;

AppErrorPresentation presentAgentChatError(Object error, AppLocalizations l10n) {
  if (error is Map) {
    final type = error['type'];
    final data = error['data'];
    if (type == 'error' && data is Map) {
      return AppErrors.present(
        AgentStreamError(Map<String, dynamic>.from(data)),
        l10n,
      );
    }
    if (type == '_error' && data is Map) {
      final status = data['status'];
      return AppErrors.present(
        ProdavanApiException(
          status is int ? status : 503,
          jsonEncode({
            'code': data['code'],
            'title': data['title'],
            'detail': data['detail'],
          }),
        ),
        l10n,
      );
    }
  }
  if (error is http.ClientException) {
    return AppErrors.present(error, l10n);
  }
  return AppErrors.present(error, l10n);
}

void showAgentChatSnack(BuildContext context, Object error) {
  if (!context.mounted) return;
  final parsed = presentAgentChatError(error, AppLocalizations.of(context));
  AppSnackBar.error(
    context,
    parsed.display,
    rawMessage: parsed.diagnostic,
  );
}
