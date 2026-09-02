import 'package:flutter_test/flutter_test.dart';
import 'package:prodavan/core/chat/thinking_duration.dart';
import 'package:prodavan/l10n/app_localizations_ru.dart';

void main() {
  final l10n = AppLocalizationsRu();

  test('formatThinkingDurationLabel hides duration under 5 seconds', () {
    expect(formatThinkingDurationLabel(l10n, durationMs: 1200), 'Размышление');
    expect(formatThinkingDurationLabel(l10n, durationMs: null), 'Размышление');
  });

  test('formatThinkingDurationLabel shows past tense with minutes and seconds', () {
    final label = formatThinkingDurationLabel(l10n, durationMs: 90000);
    expect(label, 'Размышлял 1 минуту, 30 секунд');
  });

  test('formatThinkingDurationLabel streaming', () {
    expect(formatThinkingDurationLabel(l10n, streaming: true), 'Размышление…');
  });
}
