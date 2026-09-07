import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/app_preference_tile.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_inline_add_field.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';

/// Edit `files_json` as nested prompt files: list + markdown page (no project_ids).
class PromptFilesEditorField extends StatelessWidget {
  const PromptFilesEditorField({
    super.key,
    required this.label,
    required this.value,
    required this.readOnly,
    required this.onChanged,
  });

  final String label;
  final dynamic value;
  final bool readOnly;
  final void Function(List<Map<String, dynamic>> files) onChanged;

  List<Map<String, dynamic>> _files() {
    if (value is! List) return [];
    return [
      for (final item in value)
        if (item is Map) Map<String, dynamic>.from(item),
    ];
  }

  String _newId() => 'pf_${DateTime.now().microsecondsSinceEpoch}';

  Future<void> _add(BuildContext context, String raw) async {
    final title = raw.trim();
    if (title.isEmpty) return;
    final files = _files();
    if (files.any((f) => (f['name']?.toString() ?? '') == title)) return;
    final entry = <String, dynamic>{
      'id': _newId(),
      'name': title,
      'body_md': '',
    };
    files.add(entry);
    onChanged(files);
    if (!context.mounted) return;
    await _openEditor(context, entry, files.length - 1);
  }

  void _remove(String id) {
    final files = _files().where((f) => f['id']?.toString() != id).toList();
    onChanged(files);
  }

  Future<void> _openEditor(
    BuildContext context,
    Map<String, dynamic> entry,
    int index,
  ) async {
    final result = await Navigator.of(context).push<_PromptFileEditResult>(
      MaterialPageRoute(
        builder: (_) => _PromptFileEditorPage(
          initialName: entry['name']?.toString() ?? '',
          initialBody: entry['body_md']?.toString() ??
              entry['body']?.toString() ??
              '',
          readOnly: readOnly,
        ),
      ),
    );
    if (result == null || readOnly) return;
    final files = _files();
    if (index < 0 || index >= files.length) return;
    files[index] = {
      ...files[index],
      'name': result.name,
      'body_md': result.body,
    };
    onChanged(files);
  }

  @override
  Widget build(BuildContext context) {
    final locale = Localizations.localeOf(context);
    final files = _files();
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
        for (var i = 0; i < files.length; i++)
          AppPreferenceTile(
            title: files[i]['name']?.toString() ?? '',
            icon: Icons.description_outlined,
            enabled: true,
            onTap: () => _openEditor(context, files[i], i),
            trailing: readOnly
                ? const Icon(Icons.chevron_right)
                : IconButton(
                    icon: const Icon(Icons.delete_outline),
                    onPressed: () =>
                        _remove(files[i]['id']?.toString() ?? ''),
                  ),
          ),
        if (!readOnly)
          AppInlineAddField(
            title: locale.languageCode == 'en' ? 'Add prompt…' : 'Добавить…',
            validator: (raw) => raw.trim().isNotEmpty,
            onSave: (raw) => _add(context, raw),
          ),
        if (files.isEmpty)
          Padding(
            padding: const EdgeInsets.all(AppSpacing.md),
            child: Text(
              locale.languageCode == 'en' ? 'No prompts yet' : 'Нет промптов',
              style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                    color: Theme.of(context).colorScheme.onSurfaceVariant,
                  ),
            ),
          ),
      ],
    );
  }
}

class _PromptFileEditResult {
  const _PromptFileEditResult({required this.name, required this.body});
  final String name;
  final String body;
}

class _PromptFileEditorPage extends StatefulWidget {
  const _PromptFileEditorPage({
    required this.initialName,
    required this.initialBody,
    required this.readOnly,
  });

  final String initialName;
  final String initialBody;
  final bool readOnly;

  @override
  State<_PromptFileEditorPage> createState() => _PromptFileEditorPageState();
}

class _PromptFileEditorPageState extends State<_PromptFileEditorPage> {
  late final TextEditingController _name;
  late final TextEditingController _body;

  @override
  void initState() {
    super.initState();
    _name = TextEditingController(text: widget.initialName);
    _body = TextEditingController(text: widget.initialBody);
  }

  @override
  void dispose() {
    _name.dispose();
    _body.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final locale = Localizations.localeOf(context);
    final title = _name.text.trim().isEmpty
        ? (locale.languageCode == 'en' ? 'Prompt' : 'Промпт')
        : _name.text.trim();
    return AppScaffold(
      title: Text(title),
      actions: [
        if (!widget.readOnly)
          TextButton(
            onPressed: () {
              final name = _name.text.trim();
              if (name.isEmpty) return;
              Navigator.of(context).pop(
                _PromptFileEditResult(name: name, body: _body.text),
              );
            },
            child: Text(locale.languageCode == 'en' ? 'Done' : 'Готово'),
          ),
      ],
      body: Padding(
        padding: const EdgeInsets.all(AppSpacing.md),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            TextField(
              controller: _name,
              readOnly: widget.readOnly,
              decoration: InputDecoration(
                labelText: locale.languageCode == 'en' ? 'Name' : 'Имя',
                border: const OutlineInputBorder(),
              ),
              onChanged: (_) => setState(() {}),
            ),
            const SizedBox(height: AppSpacing.md),
            Expanded(
              child: TextField(
                controller: _body,
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
                  labelText: 'Markdown',
                  alignLabelWithHint: true,
                  border: const OutlineInputBorder(),
                  filled: true,
                  fillColor: theme.colorScheme.surfaceContainerHighest
                      .withValues(alpha: 0.35),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
