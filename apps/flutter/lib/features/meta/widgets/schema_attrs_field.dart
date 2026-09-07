import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/app_value_preference.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/features/meta/meta_label.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Renders dynamic text fields from a type's `fields_json` into an `attrs` map.
class SchemaAttrsField extends StatelessWidget {
  const SchemaAttrsField({
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
        for (final field in fields)
          Builder(
            builder: (context) {
              final key = field['key']?.toString() ?? '';
              if (key.isEmpty) return const SizedBox.shrink();
              final fieldTitle = resolveMetaLabel(
                field['label'] ?? key,
                l10n,
                locale: locale,
              );
              final current = attrs[key]?.toString() ?? '';
              return AppValuePreference(
                title: fieldTitle.isEmpty ? key : fieldTitle,
                value: current,
                enabled: !readOnly,
                onSave: (v) async {
                  final next = Map<String, dynamic>.from(attrs);
                  next[key] = v;
                  onChanged(next);
                },
              );
            },
          ),
      ],
    );
  }
}

List<Map<String, dynamic>> parseFieldsJson(dynamic raw) {
  if (raw is! List) return const [];
  final out = <Map<String, dynamic>>[];
  for (final item in raw) {
    if (item is Map) {
      out.add(Map<String, dynamic>.from(item));
    }
  }
  return out;
}

Map<String, dynamic> parseAttrsMap(dynamic raw) {
  if (raw is Map) {
    return Map<String, dynamic>.from(raw);
  }
  return <String, dynamic>{};
}

String schemaAttrsSectionTitle(
  Map<String, dynamic>? fieldCfg,
  AppLocalizations l10n,
  Locale locale,
) {
  final raw = fieldCfg?['section_title'];
  final resolved = resolveMetaLabel(raw, l10n, locale: locale);
  if (resolved.isNotEmpty) return resolved;
  return locale.languageCode == 'en' ? 'Characteristics' : 'Характеристики';
}
