import 'package:flutter/foundation.dart';

/// Cross-shell request to open a chat created by a module action
/// (e.g. «Взять в работу» creates a session in the «Тендеры» project).
class ChatOpenIntent {
  const ChatOpenIntent({
    required this.projectId,
    required this.sessionId,
    this.projectName,
    this.title,
  });

  final String projectId;
  final String sessionId;
  final String? projectName;
  final String? title;
}

final ValueNotifier<ChatOpenIntent?> chatOpenIntent =
    ValueNotifier<ChatOpenIntent?>(null);
