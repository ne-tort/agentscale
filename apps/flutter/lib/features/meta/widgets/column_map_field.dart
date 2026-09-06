import 'dart:convert';

import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/app_choice_preference.dart';
import 'package:prodavan/core/preferences/app_nav_preference.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/features/meta/meta_label.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Maps source tabular headers onto a canonical target schema.
///
/// Renders as a nav preference that opens a dedicated mapping page.
///
/// Meta field shape:
/// ```json
/// {
///   "column": "column_map",
///   "widget": "column_map",
///   "source_columns_from": "columns_json",
///   "schema": [
///     {"key": "title", "label": {"ru": "Название"}, "required": true, "synonyms": ["title", "name"]}
///   ]
/// }
/// ```
class ColumnMapField extends StatefulWidget {
  const ColumnMapField({
    super.key,
    required this.label,
    required this.value,
    required this.sourceColumns,
    required this.schema,
    required this.readOnly,
    required this.onChanged,
  });

  final String label;
  final dynamic value;
  final List<String> sourceColumns;
  final List<Map<String, dynamic>> schema;
  final bool readOnly;
  final void Function(Map<String, String?> map) onChanged;

  @override
  State<ColumnMapField> createState() => _ColumnMapFieldState();
}

class _ColumnMapFieldState extends State<ColumnMapField> {
  late Map<String, String?> _map;
  bool _seeded = false;

  @override
  void initState() {
    super.initState();
    _map = _parseMap(widget.value);
    _maybeAutofill();
  }

  @override
  void didUpdateWidget(covariant ColumnMapField oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.value != widget.value) {
      _map = _parseMap(widget.value);
      _seeded = false;
      _maybeAutofill();
    } else if (!_sameList(oldWidget.sourceColumns, widget.sourceColumns)) {
      _maybeAutofill();
    }
  }

  bool _sameList(List<String> a, List<String> b) {
    if (identical(a, b)) return true;
    if (a.length != b.length) return false;
    for (var i = 0; i < a.length; i++) {
      if (a[i] != b[i]) return false;
    }
    return true;
  }

  Map<String, String?> _parseMap(dynamic raw) {
    if (raw is Map) {
      return {
        for (final e in raw.entries)
          e.key.toString(): e.value?.toString(),
      };
    }
    if (raw is String && raw.trim().isNotEmpty) {
      try {
        final decoded = jsonDecode(raw);
        if (decoded is Map) {
          return {
            for (final e in decoded.entries)
              e.key.toString(): e.value?.toString(),
          };
        }
      } catch (_) {}
    }
    return {};
  }

  List<String> _sources() {
    final seen = <String>{};
    final out = <String>[];
    for (final c in widget.sourceColumns) {
      final t = c.trim();
      if (t.isEmpty || !seen.add(t)) continue;
      out.add(t);
    }
    return out;
  }

  void _maybeAutofill() {
    if (_seeded || widget.readOnly) return;
    final sources = _sources();
    if (sources.isEmpty) return;
    final hasAny = widget.schema.any((s) {
      final key = s['key']?.toString() ?? '';
      final v = _map[key];
      return v != null && v.trim().isNotEmpty;
    });
    if (hasAny) {
      _seeded = true;
      return;
    }
    final next = Map<String, String?>.from(_map);
    var changed = false;
    for (final slot in widget.schema) {
      final key = slot['key']?.toString() ?? '';
      if (key.isEmpty) continue;
      final current = next[key];
      if (current != null && current.trim().isNotEmpty) continue;
      final synonyms = <String>[
        key,
        if (slot['synonyms'] is List)
          ...((slot['synonyms'] as List).map((e) => e.toString())),
      ];
      final match = _bestMatch(sources, synonyms);
      if (match != null) {
        next[key] = match;
        changed = true;
      }
    }
    _seeded = true;
    if (changed) {
      _map = next;
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) widget.onChanged(_cleanMap(_map));
      });
    }
  }

  String? _bestMatch(List<String> sources, List<String> synonyms) {
    final norms = {
      for (final s in synonyms) _norm(s),
    }..removeWhere((e) => e.isEmpty);
    for (final src in sources) {
      if (norms.contains(_norm(src))) return src;
    }
    for (final src in sources) {
      final n = _norm(src);
      for (final syn in norms) {
        if (n.contains(syn) || syn.contains(n)) return src;
      }
    }
    return null;
  }

  String _norm(String raw) =>
      raw.toLowerCase().replaceAll(RegExp(r'[^a-z0-9а-яё]+', unicode: true), '');

  Map<String, String?> _cleanMap(Map<String, String?> raw) {
    final out = <String, String?>{};
    for (final slot in widget.schema) {
      final key = slot['key']?.toString() ?? '';
      if (key.isEmpty) continue;
      final v = raw[key]?.trim();
      out[key] = (v == null || v.isEmpty) ? null : v;
    }
    return out;
  }

  int _mappedCount() {
    var n = 0;
    for (final slot in widget.schema) {
      final key = slot['key']?.toString() ?? '';
      final v = _map[key];
      if (v != null && v.trim().isNotEmpty) n++;
    }
    return n;
  }

  Future<void> _openEditor(BuildContext context) async {
    final l10n = AppLocalizations.of(context);
    final locale = Localizations.localeOf(context);
    final title = resolveMetaLabel(widget.label, l10n, locale: locale);
    final result = await Navigator.of(context).push<Map<String, String?>>(
      MaterialPageRoute(
        builder: (_) => _ColumnMapEditorPage(
          title: title,
          initial: Map<String, String?>.from(_map),
          sourceColumns: _sources(),
          schema: widget.schema,
          readOnly: widget.readOnly,
        ),
      ),
    );
    if (!mounted || result == null) return;
    setState(() => _map = result);
    widget.onChanged(_cleanMap(result));
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final locale = Localizations.localeOf(context);
    final title = resolveMetaLabel(widget.label, l10n, locale: locale);
    final sources = _sources();
    final mapped = _mappedCount();
    final total = widget.schema.length;
    final subtitleText = sources.isEmpty
        ? (locale.languageCode == 'ru' ? 'Нет столбцов в файле' : 'No columns in file')
        : (locale.languageCode == 'ru'
            ? '$mapped из $total · ${sources.length} столбцов файла'
            : '$mapped of $total · ${sources.length} file columns');

    return AppNavPreference(
      title: title,
      icon: Icons.table_chart_outlined,
      enabled: !widget.readOnly || mapped > 0,
      subtitle: Text(subtitleText),
      onTap: () => _openEditor(context),
    );
  }
}

class _ColumnMapEditorPage extends StatefulWidget {
  const _ColumnMapEditorPage({
    required this.title,
    required this.initial,
    required this.sourceColumns,
    required this.schema,
    required this.readOnly,
  });

  final String title;
  final Map<String, String?> initial;
  final List<String> sourceColumns;
  final List<Map<String, dynamic>> schema;
  final bool readOnly;

  @override
  State<_ColumnMapEditorPage> createState() => _ColumnMapEditorPageState();
}

class _ColumnMapEditorPageState extends State<_ColumnMapEditorPage> {
  late Map<String, String?> _map;

  @override
  void initState() {
    super.initState();
    _map = Map<String, String?>.from(widget.initial);
  }

  void _setSlot(String key, String? source) {
    setState(() {
      _map = {
        ..._map,
        key: (source == null || source.isEmpty) ? null : source,
      };
    });
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final locale = Localizations.localeOf(context);
    final noneLabel = locale.languageCode == 'ru' ? 'Не использовать' : 'Unused';
    final sources = widget.sourceColumns;

    return AppScaffold(
      title: Text(widget.title),
      actions: [
        if (!widget.readOnly)
          TextButton(
            onPressed: () => Navigator.of(context).pop(_map),
            child: Text(locale.languageCode == 'ru' ? 'Готово' : 'Done'),
          ),
      ],
      body: ListView(
        padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
        children: [
          for (final slot in widget.schema) ...[
            Builder(
              builder: (context) {
                final key = slot['key']?.toString() ?? '';
                if (key.isEmpty) return const SizedBox.shrink();
                final required = slot['required'] == true;
                final slotLabel = resolveMetaLabel(
                  slot['label'] ?? key,
                  l10n,
                  locale: locale,
                );
                final choices = <String>[
                  if (!required) '',
                  ...sources,
                ];
                final current = _map[key] ?? '';
                final value = choices.contains(current)
                    ? current
                    : (required
                        ? (choices.isNotEmpty ? choices.first : '')
                        : '');
                return AppChoicePreference<String>(
                  title: slotLabel,
                  icon: required ? Icons.link_rounded : Icons.link_off_outlined,
                  value: value,
                  choices: choices,
                  keyFor: (v) => v,
                  labelFor: (v) => v.isEmpty ? noneLabel : v,
                  enabled: !widget.readOnly && sources.isNotEmpty,
                  onSave: (v) async => _setSlot(key, v.isEmpty ? null : v),
                );
              },
            ),
          ],
        ],
      ),
    );
  }
}

/// Parse `columns_json` body field into header names.
List<String> parseColumnsJson(dynamic raw) {
  if (raw is List) {
    return raw.map((e) => e.toString()).where((e) => e.isNotEmpty).toList();
  }
  if (raw is String && raw.trim().isNotEmpty) {
    try {
      final decoded = jsonDecode(raw);
      if (decoded is List) {
        return decoded.map((e) => e.toString()).where((e) => e.isNotEmpty).toList();
      }
    } catch (_) {}
  }
  return const [];
}
