import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/app_preference_tile.dart';
import 'package:prodavan/core/preferences/app_value_preference.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_inline_add_field.dart';
import 'package:prodavan/core/widgets/app_multiline_text_field.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';

/// Edit `files_json` as nested prompt files: list + markdown editor page.
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

  void _emit(List<Map<String, dynamic>> files) => onChanged(files);

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
    _emit(files);
    if (!context.mounted) return;
    await _openEditor(context, files.length - 1);
  }

  void _remove(String id) {
    _emit(_files().where((f) => f['id']?.toString() != id).toList());
  }

  Future<void> _openEditor(BuildContext context, int index) async {
    final files = _files();
    if (index < 0 || index >= files.length) return;
    final entry = Map<String, dynamic>.from(files[index]);
    final locale = Localizations.localeOf(context);

    await Navigator.of(context).push<void>(
      MaterialPageRoute(
        builder: (_) => _PromptFileEditorPage(
          initialName: entry['name']?.toString() ?? '',
          initialBody: entry['body_md']?.toString() ??
              entry['body']?.toString() ??
              '',
          readOnly: readOnly,
          nameLabel: locale.languageCode == 'en' ? 'Name' : 'Имя',
          onCommit: (name, body) {
            final next = _files();
            if (index < 0 || index >= next.length) return;
            next[index] = {
              ...next[index],
              'name': name,
              'body_md': body,
            };
            _emit(next);
          },
        ),
      ),
    );
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
            onTap: () => _openEditor(context, i),
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
      ],
    );
  }
}

class _PromptFileEditorPage extends StatefulWidget {
  const _PromptFileEditorPage({
    required this.initialName,
    required this.initialBody,
    required this.readOnly,
    required this.nameLabel,
    required this.onCommit,
  });

  final String initialName;
  final String initialBody;
  final bool readOnly;
  final String nameLabel;
  final void Function(String name, String body) onCommit;

  @override
  State<_PromptFileEditorPage> createState() => _PromptFileEditorPageState();
}

class _PromptFileEditorPageState extends State<_PromptFileEditorPage> {
  late String _name;
  late String _body;
  final _bodyFocus = FocusNode();

  @override
  void initState() {
    super.initState();
    _name = widget.initialName;
    _body = widget.initialBody;
  }

  @override
  void dispose() {
    _bodyFocus.dispose();
    super.dispose();
  }

  void _flush() {
    if (widget.readOnly) return;
    final name = _name.trim();
    if (name.isEmpty) return;
    widget.onCommit(name, _body);
  }

  void _pop() {
    _flush();
    if (mounted) Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
    final locale = Localizations.localeOf(context);
    final title = _name.trim().isEmpty
        ? (locale.languageCode == 'en' ? 'Prompt' : 'Промпт')
        : _name.trim();
    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, result) {
        if (didPop) return;
        _pop();
      },
      child: AppScaffold(
        title: Text(title),
        body: Padding(
          padding: const EdgeInsets.all(AppSpacing.md),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              AppValuePreference<String>(
                title: widget.nameLabel,
                value: _name,
                icon: Icons.badge_outlined,
                enabled: !widget.readOnly,
                onSave: (v) async {
                  setState(() => _name = v.trim());
                  _flush();
                },
              ),
              const SizedBox(height: AppSpacing.md),
              Expanded(
                child: AppMultilineTextField(
                  value: _body,
                  readOnly: widget.readOnly,
                  markdown: true,
                  expands: true,
                  focusNode: _bodyFocus,
                  onChanged: (v) {
                    _body = v;
                    _flush();
                  },
                  onEditingComplete: _flush,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
