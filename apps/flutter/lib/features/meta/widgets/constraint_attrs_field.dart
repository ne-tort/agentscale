import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/app_choice_preference.dart';
import 'package:prodavan/core/preferences/app_value_preference.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/features/meta/meta_label.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Значения характеристик по шаблону типа (`fields_json`): поля с заранее
/// заданными значениями (kind enum/groups) — выбор из списка, остальные —
/// текст. Обязательные поля (required) помечены «*». Используется и для
/// `part_cards.attrs`, и для `ready_build_slots.constraints` — семантика
/// одна: значения только из групп типа.
class ConstraintAttrsField extends StatelessWidget {
  const ConstraintAttrsField({
    super.key,
    required this.fields,
    required this.attrs,
    required this.readOnly,
    required this.onChanged,
    this.sectionTitle,
  });

  final List<Map<String, dynamic>> fields;
  final Map<String, dynamic> attrs;
  final bool readOnly;
  final void Function(Map<String, dynamic> attrs) onChanged;
  final String? sectionTitle;

  @override
  Widget build(BuildContext context) {
    if (fields.isEmpty) return const SizedBox.shrink();
    final l10n = AppLocalizations.of(context);
    final locale = Localizations.localeOf(context);
    final title = (sectionTitle != null && sectionTitle!.trim().isNotEmpty)
        ? sectionTitle!.trim()
        : (locale.languageCode == 'en' ? 'Characteristics' : 'Характеристики');
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(
            AppSpacing.md,
            AppSpacing.sm,
            AppSpacing.md,
            AppSpacing.xs,
          ),
          child: Text(
            title,
            style: Theme.of(context).textTheme.titleSmall,
          ),
        ),
        for (final field in fields) _field(context, field, l10n, locale),
      ],
    );
  }

  Widget _field(
    BuildContext context,
    Map<String, dynamic> field,
    AppLocalizations l10n,
    Locale locale,
  ) {
    final key = field['key']?.toString() ?? '';
    if (key.isEmpty) return const SizedBox.shrink();
    var fieldTitle = resolveMetaLabel(field['label'] ?? key, l10n, locale: locale);
    if (fieldTitle.isEmpty) fieldTitle = key;
    if (field['required'] == true) fieldTitle = '$fieldTitle *';
    final current = attrs[key]?.toString() ?? '';

    // kind=enum: values — плоский список строк; kind=groups: value+label.
    final choices = _choicesOf(field);
    if (choices.isEmpty) {
      return AppValuePreference(
        title: fieldTitle,
        value: current,
        enabled: !readOnly,
        onSave: (v) async {
          final next = Map<String, dynamic>.from(attrs);
          final trimmed = v.trim();
          if (trimmed.isEmpty) {
            next.remove(key);
          } else {
            next[key] = trimmed;
          }
          onChanged(next);
        },
      );
    }
    final known = choices.any((c) => c.key == current);
    final allChoices = known || current.isEmpty
        ? choices
        : [...choices, MapEntry(current, current)];
    final currentEntry = allChoices.firstWhere(
      (c) => c.key == current,
      orElse: () => const MapEntry('', '—'),
    );
    return AppChoicePreference<MapEntry<String, String>>(
      title: fieldTitle,
      // неизвестное значение (устаревшая группа после правки типа) показываем
      // как есть, но не подсовываем его в список выбора
      value: currentEntry,
      choices: allChoices,
      keyFor: (e) => e.key,
      labelFor: (e) => e.value,
      enabled: !readOnly,
      onSave: (e) async {
        final next = Map<String, dynamic>.from(attrs);
        if (e.key.isEmpty) {
          next.remove(key);
        } else {
          next[key] = e.key;
        }
        onChanged(next);
      },
    );
  }

  List<MapEntry<String, String>> _choicesOf(Map<String, dynamic> field) {
    final kind = field['kind']?.toString() ?? 'text';
    final out = <MapEntry<String, String>>[];
    if (kind == 'enum') {
      final values = field['values'];
      if (values is List) {
        for (final v in values) {
          final s = v?.toString().trim() ?? '';
          if (s.isNotEmpty) out.add(MapEntry(s, s));
        }
      }
    } else if (kind == 'groups') {
      final groups = field['groups'];
      if (groups is List) {
        for (final g in groups) {
          if (g is Map) {
            final value = g['value']?.toString().trim() ?? '';
            if (value.isEmpty) continue;
            final label = g['label']?.toString().trim() ?? '';
            out.add(MapEntry(value, label.isEmpty ? value : label));
          } else {
            final s = g?.toString().trim() ?? '';
            if (s.isNotEmpty) out.add(MapEntry(s, s));
          }
        }
      }
    }
    return out;
  }
}
