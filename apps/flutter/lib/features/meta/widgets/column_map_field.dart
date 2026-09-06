import 'dart:convert';

import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/app_choice_preference.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/features/meta/meta_label.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Maps source tabular headers onto a canonical target schema.
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
    } else if (oldWidget.sourceColumns != widget.sourceColumns) {
      _maybeAutofill();
    }
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

  void _setSlot(String key, String? source) {
    setState(() {
      _map = {
        ..._map,
        key: (source == null || source.isEmpty) ? null : source,
      };
    });
    widget.onChanged(_cleanMap(_map));
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final locale = Localizations.localeOf(context);
    final sources = _sources();
    final noneLabel = locale.languageCode == 'ru' ? 'Не использовать' : 'Unused';

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
            resolveMetaLabel(widget.label, l10n, locale: locale),
            style: Theme.of(context).textTheme.titleSmall,
          ),
        ),
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
              final value = choices.contains(current) ? current : (required ? (choices.isNotEmpty ? choices.first : '') : '');
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
