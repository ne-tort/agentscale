import 'package:prodavan/l10n/app_localizations.dart';

/// Human-readable thinking duration for chat blocks (minimum 5 seconds to show).
String formatThinkingDurationLabel(AppLocalizations l10n, {int? durationMs, bool streaming = false}) {
  if (streaming) return l10n.projectChatReasoningStreaming;
  if (durationMs == null || durationMs < 5000) return l10n.projectChatReasoning;
  final totalSeconds = (durationMs / 1000).round();
  final minutes = totalSeconds ~/ 60;
  final seconds = totalSeconds % 60;
  final parts = <String>[];
  if (minutes > 0) parts.add(_formatMinutes(l10n, minutes));
  if (seconds > 0 || parts.isEmpty) parts.add(_formatSeconds(l10n, seconds));
  return l10n.projectChatReasonedPast(parts.join(', '));
}

String _formatMinutes(AppLocalizations l10n, int count) {
  final locale = l10n.localeName;
  if (locale.startsWith('ru')) {
    final mod10 = count % 10;
    final mod100 = count % 100;
    if (mod10 == 1 && mod100 != 11) return '$count минуту';
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return '$count минуты';
    return '$count минут';
  }
  return count == 1 ? '1 minute' : '$count minutes';
}

String _formatSeconds(AppLocalizations l10n, int count) {
  final locale = l10n.localeName;
  if (locale.startsWith('ru')) {
    final mod10 = count % 10;
    final mod100 = count % 100;
    if (mod10 == 1 && mod100 != 11) return '$count секунду';
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return '$count секунды';
    return '$count секунд';
  }
  return count == 1 ? '1 second' : '$count seconds';
}
