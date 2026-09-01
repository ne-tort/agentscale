import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/features/employee/project_chat_controller.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Localize agent SSE/API errors for chat UI.
String localizeAgentChatError(Object error, AppLocalizations l10n) {
  if (error is ProdavanApiException) {
    return AppErrors.present(error, l10n).display;
  }
  if (error is AgentStreamError) {
    final code = error.data['code'] as String?;
    if (code != null && code.isNotEmpty) {
      final fake = ProdavanApiException(
        503,
        '{"code":"$code","detail":"${error.data['message'] ?? ''}"}',
      );
      final mapped = AppErrors.present(fake, l10n).display;
      if (mapped != l10n.errorUnexpected) return mapped;
    }
    final message = error.data['message']?.toString();
    if (message != null && message.isNotEmpty) return message;
    return l10n.errorAgentRuntimeUnavailable;
  }
  return AppErrors.present(error, l10n).display;
}
