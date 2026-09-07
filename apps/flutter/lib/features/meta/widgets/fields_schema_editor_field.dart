import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/app_preference_tile.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_inline_add_field.dart';

/// Edit `fields_json` as a list of {key, label} text characteristics.
class FieldsSchemaEditorField extends StatelessWidget {
  const FieldsSchemaEditorField({
    super.key,
    required this.label,
    required this.value,
    required this.readOnly,
    required this.onChanged,
  });

  final String label;
  final dynamic value;
  final bool readOnly;
  final void Function(List<Map<String, dynamic>> fields) onChanged;

  List<Map<String, dynamic>> _fields() {
    if (value is! List) return [];
    return [
      for (final item in value)
        if (item is Map) Map<String, dynamic>.from(item),
    ];
  }

  Future<void> _add(String raw) async {
    final title = raw.trim();
    if (title.isEmpty) return;
    final key = slugifyFieldKey(title);
    final fields = _fields();
    if (fields.any((f) => f['key']?.toString() == key)) return;
    fields.add({
      'key': key,
      'label': {'ru': title, 'en': title},
    });
    onChanged(fields);
  }

  void _remove(String key) {
    final fields = _fields().where((f) => f['key']?.toString() != key).toList();
    onChanged(fields);
  }

  @override
  Widget build(BuildContext context) {
    final locale = Localizations.localeOf(context);
    final fields = _fields();
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
            label,
            style: Theme.of(context).textTheme.titleSmall,
          ),
        ),
        for (final field in fields)
          AppPreferenceTile(
            title: _fieldTitle(field, locale),
            icon: Icons.input,
            subtitle: Text(field['key']?.toString() ?? ''),
            enabled: !readOnly,
            trailing: readOnly
                ? null
                : IconButton(
                    icon: const Icon(Icons.delete_outline),
                    onPressed: () => _remove(field['key']?.toString() ?? ''),
                  ),
          ),
        if (!readOnly)
          AppInlineAddField(
            title: locale.languageCode == 'en'
                ? 'Add characteristic'
                : 'Добавить характеристику',
            validator: (raw) => raw.trim().isNotEmpty,
            onSave: _add,
          ),
        if (fields.isEmpty)
          Padding(
            padding: const EdgeInsets.all(AppSpacing.md),
            child: Text(
              locale.languageCode == 'en'
                  ? 'No characteristics yet'
                  : 'Нет характеристик',
              style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                    color: Theme.of(context).colorScheme.onSurfaceVariant,
                  ),
            ),
          ),
      ],
    );
  }

  String _fieldTitle(Map<String, dynamic> field, Locale locale) {
    final label = field['label'];
    if (label is Map) {
      final lang = label[locale.languageCode];
      if (lang is String && lang.isNotEmpty) return lang;
      if (label['ru'] is String) return label['ru'] as String;
      if (label['en'] is String) return label['en'] as String;
    }
    if (label is String && label.isNotEmpty) return label;
    return field['key']?.toString() ?? '';
  }
}

/// Latin snake_case key from a human label (Cyrillic → simple translit).
String slugifyFieldKey(String raw) {
  const map = {
    'а': 'a',
    'б': 'b',
    'в': 'v',
    'г': 'g',
    'д': 'd',
    'е': 'e',
    'ё': 'e',
    'ж': 'zh',
    'з': 'z',
    'и': 'i',
    'й': 'y',
    'к': 'k',
    'л': 'l',
    'м': 'm',
    'н': 'n',
    'о': 'o',
    'п': 'p',
    'р': 'r',
    'с': 's',
    'т': 't',
    'у': 'u',
    'ф': 'f',
    'х': 'h',
    'ц': 'ts',
    'ч': 'ch',
    'ш': 'sh',
    'щ': 'sch',
    'ъ': '',
    'ы': 'y',
    'ь': '',
    'э': 'e',
    'ю': 'yu',
    'я': 'ya',
  };
  final buf = StringBuffer();
  for (final rune in raw.trim().toLowerCase().runes) {
    final ch = String.fromCharCode(rune);
    if (map.containsKey(ch)) {
      buf.write(map[ch]);
    } else if (RegExp(r'[a-z0-9]').hasMatch(ch)) {
      buf.write(ch);
    } else if (ch == ' ' || ch == '-' || ch == '/' || ch == '.') {
      buf.write('_');
    }
  }
  var key = buf
      .toString()
      .replaceAll(RegExp(r'_+'), '_')
      .replaceAll(RegExp(r'^_|_$'), '');
  if (key.isEmpty) key = 'field';
  if (RegExp(r'^[0-9]').hasMatch(key)) key = 'f_$key';
  if (key.length > 48) key = key.substring(0, 48);
  return key;
}
