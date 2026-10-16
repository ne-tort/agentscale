import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_inline_add_field.dart';

/// Редактор `fields_json` типа комплектующего: список характеристик
/// {key, label, kind, required, compat_domain, values|groups}.
///
/// kind: text (свободное значение), enum (значение из values),
/// groups (значение из groups — «диапазон» типа «512 ГБ»). required — поле
/// обязательно в карточке комплектующего и участвует в подборе. compat_domain
/// связывает типы в матрицу совместимости (socket, ram_type, form_factor).
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
      'kind': 'text',
    });
    onChanged(fields);
  }

  void _remove(String key) {
    final fields = _fields().where((f) => f['key']?.toString() != key).toList();
    onChanged(fields);
  }

  void _update(String key, Map<String, dynamic> updated) {
    final fields = [
      for (final f in _fields())
        if (f['key']?.toString() == key) updated else f,
    ];
    onChanged(fields);
  }

  @override
  Widget build(BuildContext context) {
    final locale = Localizations.localeOf(context);
    final isEn = locale.languageCode == 'en';
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
          _FieldTile(
            field: field,
            readOnly: readOnly,
            isEn: isEn,
            onChanged: (updated) => _update(field['key']?.toString() ?? '', updated),
            onRemove: () => _remove(field['key']?.toString() ?? ''),
          ),
        if (!readOnly)
          AppInlineAddField(
            title: isEn ? 'Add characteristic' : 'Добавить характеристику',
            validator: (raw) => raw.trim().isNotEmpty,
            onSave: _add,
          ),
        if (fields.isEmpty)
          Padding(
            padding: const EdgeInsets.all(AppSpacing.md),
            child: Text(
              isEn ? 'No characteristics yet' : 'Нет характеристик',
              style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                    color: Theme.of(context).colorScheme.onSurfaceVariant,
                  ),
            ),
          ),
      ],
    );
  }
}

class _FieldTile extends StatefulWidget {
  const _FieldTile({
    required this.field,
    required this.readOnly,
    required this.isEn,
    required this.onChanged,
    required this.onRemove,
  });

  final Map<String, dynamic> field;
  final bool readOnly;
  final bool isEn;
  final void Function(Map<String, dynamic> field) onChanged;
  final VoidCallback onRemove;

  @override
  State<_FieldTile> createState() => _FieldTileState();
}

class _FieldTileState extends State<_FieldTile> {
  late final TextEditingController _domainCtrl;
  late final TextEditingController _valuesCtrl;
  late final TextEditingController _groupsCtrl;

  @override
  void initState() {
    super.initState();
    _domainCtrl = TextEditingController(
      text: widget.field['compat_domain']?.toString() ?? '',
    );
    _valuesCtrl = TextEditingController(text: _valuesText(widget.field));
    _groupsCtrl = TextEditingController(text: _groupsText(widget.field));
  }

  @override
  void dispose() {
    _domainCtrl.dispose();
    _valuesCtrl.dispose();
    _groupsCtrl.dispose();
    super.dispose();
  }

  String _valuesText(Map<String, dynamic> field) {
    final values = field['values'];
    if (values is! List) return '';
    return values.map((v) => v?.toString() ?? '').where((v) => v.isNotEmpty).join(', ');
  }

  String _groupsText(Map<String, dynamic> field) {
    final groups = field['groups'];
    if (groups is! List) return '';
    return groups
        .map((g) {
          if (g is Map) {
            final v = g['value']?.toString() ?? '';
            final l = g['label']?.toString() ?? '';
            return v.isEmpty ? '' : (l.isEmpty ? v : '$v=$l');
          }
          return g?.toString() ?? '';
        })
        .where((v) => v.isNotEmpty)
        .join(', ');
  }

  void _save(Map<String, dynamic> patch) {
    final next = Map<String, dynamic>.from(widget.field)..addAll(patch);
    // чистим пустое, чтобы шаблон не пух
    next.removeWhere((k, v) {
      if (k == 'key' || k == 'label') return false;
      if (v == null) return true;
      if (v is String && v.trim().isEmpty) return true;
      if (v is List && v.isEmpty) return true;
      if (v == false) return true;
      return false;
    });
    widget.onChanged(next);
  }

  List<String> _parseValues(String raw) => [
        for (final part in raw.split(','))
          if (part.trim().isNotEmpty) part.trim(),
      ];

  List<Map<String, String>> _parseGroups(String raw) => [
        for (final part in raw.split(','))
          if (part.trim().isNotEmpty)
            () {
              final bits = part.split('=');
              final value = bits.first.trim();
              final label = bits.length > 1 ? bits.sublist(1).join('=').trim() : '';
              return {
                'value': value,
                if (label.isNotEmpty) 'label': label,
              };
            }(),
      ];

  @override
  Widget build(BuildContext context) {
    final field = widget.field;
    final isEn = widget.isEn;
    final kind = field['kind']?.toString() ?? 'text';
    final required = field['required'] == true;
    final domain = field['compat_domain']?.toString() ?? '';
    final summary = <String>[
      switch (kind) {
        'enum' => isEn ? 'list' : 'список',
        'groups' => isEn ? 'groups' : 'группы',
        _ => isEn ? 'text' : 'текст',
      },
      if (required) (isEn ? 'required' : 'обязательное'),
      if (domain.isNotEmpty) domain,
    ].join(' · ');
    return ExpansionTile(
      title: Text(_fieldTitle(field, Localizations.localeOf(context))),
      subtitle: Text('${field['key'] ?? ''} · $summary'),
      tilePadding: const EdgeInsets.symmetric(horizontal: AppSpacing.md),
      childrenPadding: const EdgeInsets.fromLTRB(
        AppSpacing.md,
        0,
        AppSpacing.md,
        AppSpacing.sm,
      ),
      children: [
        // kind: текст / список значений / группы
        Padding(
          padding: const EdgeInsets.only(bottom: AppSpacing.xs),
          child: Wrap(
            spacing: AppSpacing.xs,
            children: [
              for (final k in ['text', 'enum', 'groups'])
                ChoiceChip(
                  label: Text(switch (k) {
                    'text' => (isEn ? 'Text' : 'Текст'),
                    'enum' => (isEn ? 'Value list' : 'Список значений'),
                    _ => (isEn ? 'Groups (ranges)' : 'Группы (диапазоны)'),
                  }),
                  selected: kind == k,
                  onSelected: widget.readOnly
                      ? null
                      : (_) {
                          final patch = <String, dynamic>{'kind': k};
                          if (k == 'enum') {
                            patch['values'] = _parseValues(_valuesCtrl.text);
                            patch['groups'] = null;
                          } else if (k == 'groups') {
                            patch['groups'] = _parseGroups(_groupsCtrl.text);
                            patch['values'] = null;
                          } else {
                            patch['values'] = null;
                            patch['groups'] = null;
                          }
                          _save(patch);
                        },
                ),
            ],
          ),
        ),
        SwitchListTile(
          contentPadding: EdgeInsets.zero,
          title: Text(isEn ? 'Required (compatibility)' : 'Обязательное (совместимость)'),
          value: required,
          onChanged:
              widget.readOnly ? null : (v) => _save({'required': v ? true : null}),
        ),
        TextField(
          controller: _domainCtrl,
          enabled: !widget.readOnly,
          decoration: InputDecoration(
            labelText: isEn
                ? 'Compatibility domain (socket, ram_type, form_factor…)'
                : 'Домен совместимости (socket, ram_type, form_factor…)',
            isDense: true,
          ),
          onChanged: (v) => _save({'compat_domain': v.trim()}),
        ),
        if (kind == 'enum')
          TextField(
            controller: _valuesCtrl,
            enabled: !widget.readOnly,
            decoration: InputDecoration(
              labelText: isEn
                  ? 'Values (comma-separated)'
                  : 'Значения (через запятую)',
              hintText: 'LGA1700, AM5',
              isDense: true,
            ),
            onChanged: (v) => _save({'values': _parseValues(v)}),
          ),
        if (kind == 'groups')
          TextField(
            controller: _groupsCtrl,
            enabled: !widget.readOnly,
            decoration: InputDecoration(
              labelText: isEn
                  ? 'Groups: value=label, comma-separated'
                  : 'Группы: значение=подпись, через запятую',
              hintText: '512=512 ГБ, 1024=1024 ГБ',
              isDense: true,
            ),
            onChanged: (v) => _save({'groups': _parseGroups(v)}),
          ),
        if (!widget.readOnly)
          Align(
            alignment: Alignment.centerRight,
            child: IconButton(
              icon: const Icon(Icons.delete_outline),
              onPressed: widget.onRemove,
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
