import 'package:flutter/foundation.dart';

import 'package:prodavan/core/errors/app_failure.dart';

enum UiMessageKind { error, success, info }

class UiMessage {
  const UiMessage({required this.kind, required this.text});

  final UiMessageKind kind;
  final String text;
}

/// Global toast/snack bus. Screens must not show raw API dumps.
class UiMessenger extends ChangeNotifier {
  UiMessage? _pending;

  UiMessage? get pending => _pending;

  void showError(String message) => _emit(UiMessageKind.error, message);

  void showSuccess(String message) => _emit(UiMessageKind.success, message);

  void showInfo(String message) => _emit(UiMessageKind.info, message);

  void showFailure(AppFailure failure) => showError(failure.message);

  void _emit(UiMessageKind kind, String text) {
    final trimmed = text.trim();
    if (trimmed.isEmpty) return;
    _pending = UiMessage(kind: kind, text: trimmed);
    notifyListeners();
  }

  /// Called by [AppSnackHost] after presenting the snack.
  void clearPending() {
    _pending = null;
  }
}
