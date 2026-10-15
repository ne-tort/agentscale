import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/app_value_preference.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';

/// Разбор строки целевого коридора «100-10000».
///
/// - «100-10000» → (100, 10000);
/// - одно число «100» → (100, null) — минимум;
/// - «0-10000» → (0, 10000) — по смыслу «до 10000»;
/// - пустая строка → (null, null) — очистить;
/// - разделитель дробной части «,» или «.»; лишние знаки после запятой
///   (больше двух) отсекаются строкой, без float-ошибок;
/// - пробелы/неразрывные пробелы и тире «–», «—», «−» нормализуются.
///
/// Возвращает null, если строку разобрать нельзя.
(num?, num?)? parseTargetPriceRange(String raw) {
  final cleaned = raw
      .replaceAll(RegExp(r'[\s ]+'), '')
      .replaceAll('–', '-')
      .replaceAll('—', '-')
      .replaceAll('−', '-');
  if (cleaned.isEmpty) return const (null, null);

  num? parseNum(String s) {
    if (s.isEmpty) return null;
    final norm = s.replaceAll(',', '.');
    final v = double.tryParse(norm);
    if (v == null || v < 0 || !v.isFinite) return null;
    final dot = norm.indexOf('.');
    if (dot >= 0 && norm.length - dot - 1 > 2) {
      final cut = double.parse(norm.substring(0, dot + 3));
      return cut == cut.roundToDouble() ? cut.round() : cut;
    }
    return v == v.roundToDouble() ? v.round() : v;
  }

  final parts = cleaned.split('-');
  if (parts.length > 2) return null;
  if (parts.length == 1) {
    final v = parseNum(parts[0]);
    return v == null ? null : (v, null);
  }
  final min = parseNum(parts[0]);
  final max = parseNum(parts[1]);
  if (parts[0].isNotEmpty && min == null) return null;
  if (parts[1].isNotEmpty && max == null) return null;
  if (min == null && max == null) return null;
  return (min, max);
}

/// Отображение коридора в одну строку: «100-10000», «100», «0-10000».
String formatTargetPriceRange(num? min, num? max) {
  String f(num v) => v == v.roundToDouble() ? v.toInt().toString() : v.toString();
  if (min == null && max == null) return '';
  if (max == null) return f(min!);
  return '${f(min ?? 0)}-${f(max)}';
}

/// Целевой коридор цены сборки: одно поле «100-10000» (такое же, как
/// «название») + справа зелёным — коридор возможных комбинаций
/// (price_min..price_max из пайплайна), виден в спокойном состоянии.
class TargetPriceRangeField extends StatelessWidget {
  const TargetPriceRangeField({
    super.key,
    required this.label,
    required this.valueMin,
    required this.valueMax,
    required this.priceMin,
    required this.priceMax,
    required this.readOnly,
    required this.onChanged,
    this.icon,
    this.hint,
  });

  final String label;
  final num? valueMin;
  final num? valueMax;
  final num? priceMin;
  final num? priceMax;
  final bool readOnly;
  final void Function(num? min, num? max) onChanged;
  final IconData? icon;
  final String? hint;

  static const _invalidMessage =
      'Формат: 100-10000 (от–до); одно число — минимум; 0-10000 — не дороже';

  @override
  Widget build(BuildContext context) {
    final colors = context.appColors;
    final hasCorridor = priceMin != null || priceMax != null;
    return AppValuePreference<String>(
      title: label,
      icon: icon,
      value: formatTargetPriceRange(valueMin, valueMax),
      enabled: !readOnly,
      hintText: hint ?? _invalidMessage,
      keyboardType: TextInputType.text,
      validateInput: (raw) => parseTargetPriceRange(raw) != null,
      invalidMessage: _invalidMessage,
      inputToValue: (raw) => raw,
      trailing: hasCorridor
          ? Text(
              '${formatTargetPriceRange(priceMin, priceMax)} ₽',
              style: Theme.of(context).textTheme.titleSmall?.copyWith(
                    color: colors.success,
                    fontWeight: FontWeight.w600,
                  ),
            )
          : null,
      onSave: (raw) async {
        final parsed = parseTargetPriceRange(raw);
        if (parsed == null) return;
        onChanged(parsed.$1, parsed.$2);
      },
    );
  }
}
