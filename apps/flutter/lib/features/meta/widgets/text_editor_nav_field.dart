import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/app_nav_preference.dart';
import 'package:prodavan/core/widgets/app_multiline_editor_page.dart';
import 'package:prodavan/features/meta/meta_icon.dart';
import 'package:prodavan/features/meta/meta_label.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Preference row that opens a full-page multiline text editor.
class TextEditorNavField extends StatelessWidget {
  const TextEditorNavField({
    super.key,
    required this.label,
    required this.value,
    required this.readOnly,
    required this.onChanged,
    this.icon,
    this.emptyLabel,
    this.markdown = false,
  });

  final String label;
  final String value;
  final bool readOnly;
  final ValueChanged<String> onChanged;
  final IconData? icon;
  final String? emptyLabel;
  final bool markdown;

  @override
  Widget build(BuildContext context) {
    final locale = Localizations.localeOf(context);
    final empty = emptyLabel ??
        (locale.languageCode == 'en' ? 'Not set' : 'Не задано');
    final trimmed = value.trim();
    final preview = trimmed.isEmpty
        ? empty
        : (trimmed.length > 80 ? '${trimmed.substring(0, 80)}…' : trimmed);
    return AppNavPreference(
      title: label,
      icon: icon ?? Icons.notes_outlined,
      subtitle: Text(preview),
      enabled: !readOnly || trimmed.isNotEmpty,
      onTap: () => _openEditor(context),
    );
  }

  Future<void> _openEditor(BuildContext context) async {
    final result = await Navigator.of(context).push<String>(
      MaterialPageRoute(
        builder: (_) => AppMultilineEditorPage(
          title: label,
          initial: value,
          readOnly: readOnly,
          markdown: markdown,
          onChanged: readOnly ? null : onChanged,
        ),
      ),
    );
    if (result == null || readOnly) return;
    onChanged(result);
  }
}

String textEditorEmptyLabel(
  Map<String, dynamic>? fieldCfg,
  AppLocalizations l10n,
  Locale locale,
) {
  final raw = fieldCfg?['empty_label'];
  final resolved = resolveMetaLabel(raw, l10n, locale: locale);
  if (resolved.isNotEmpty) return resolved;
  return locale.languageCode == 'en' ? 'Not set' : 'Не задано';
}

IconData textEditorIcon(String? name) =>
    metaIconFromName(name, fallback: Icons.notes_outlined);
