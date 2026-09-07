import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/app_nav_preference.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/features/meta/meta_icon.dart';
import 'package:prodavan/features/meta/meta_label.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Preference row that opens a full-page multiline text editor (prompts-style).
class TextEditorNavField extends StatelessWidget {
  const TextEditorNavField({
    super.key,
    required this.label,
    required this.value,
    required this.readOnly,
    required this.onChanged,
    this.icon,
    this.emptyLabel,
  });

  final String label;
  final String value;
  final bool readOnly;
  final ValueChanged<String> onChanged;
  final IconData? icon;
  final String? emptyLabel;

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
        builder: (_) => _TextEditorPage(
          title: label,
          initial: value,
          readOnly: readOnly,
        ),
      ),
    );
    if (result == null || readOnly) return;
    onChanged(result);
  }
}

class _TextEditorPage extends StatefulWidget {
  const _TextEditorPage({
    required this.title,
    required this.initial,
    required this.readOnly,
  });

  final String title;
  final String initial;
  final bool readOnly;

  @override
  State<_TextEditorPage> createState() => _TextEditorPageState();
}

class _TextEditorPageState extends State<_TextEditorPage> {
  late final TextEditingController _controller;

  @override
  void initState() {
    super.initState();
    _controller = TextEditingController(text: widget.initial);
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final locale = Localizations.localeOf(context);
    return AppScaffold(
      title: Text(widget.title),
      actions: [
        if (!widget.readOnly)
          TextButton(
            onPressed: () => Navigator.of(context).pop(_controller.text),
            child: Text(locale.languageCode == 'en' ? 'Done' : 'Готово'),
          ),
      ],
      body: Padding(
        padding: const EdgeInsets.all(AppSpacing.md),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Expanded(
              child: TextField(
                controller: _controller,
                readOnly: widget.readOnly,
                expands: true,
                maxLines: null,
                minLines: null,
                textAlignVertical: TextAlignVertical.top,
                style: theme.textTheme.bodyMedium?.copyWith(
                  fontFamily: 'monospace',
                  height: 1.45,
                ),
                decoration: InputDecoration(
                  border: const OutlineInputBorder(),
                  filled: true,
                  fillColor: theme.colorScheme.surfaceContainerHighest
                      .withValues(alpha: 0.35),
                  alignLabelWithHint: true,
                ),
              ),
            ),
          ],
        ),
      ),
    );
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
