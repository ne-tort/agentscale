import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/app_value_preference.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_inline_add_field.dart';
import 'package:prodavan/core/widgets/app_multiline_text_field.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';

/// Edit `files_json` as nested prompt files: table + markdown editor page.
class PromptFilesEditorField extends StatelessWidget {
  const PromptFilesEditorField({
    super.key,
    required this.value,
    required this.readOnly,
    required this.onChanged,
  });

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

  List<Map<String, dynamic>> _sorted(List<Map<String, dynamic>> files) {
    final indexed = [for (var i = 0; i < files.length; i++) (i, files[i])];
    indexed.sort((a, b) {
      final pa = _priorityOf(a.$2);
      final pb = _priorityOf(b.$2);
      if (pa != pb) return pa.compareTo(pb);
      return a.$1.compareTo(b.$1);
    });
    return [for (final e in indexed) e.$2];
  }

  static int _priorityOf(Map<String, dynamic> entry) {
    final raw = entry['priority'];
    if (raw is int) return raw;
    if (raw is num) return raw.toInt();
    if (raw is String) return int.tryParse(raw.trim()) ?? 100;
    return 100;
  }

  int _nextPriority(List<Map<String, dynamic>> files) {
    var maxP = 99;
    for (final f in files) {
      final p = _priorityOf(f);
      if (p > maxP) maxP = p;
    }
    return maxP + 1;
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
      'priority': _nextPriority(files),
      'body_md': '',
    };
    files.add(entry);
    _emit(files);
    if (!context.mounted) return;
    await _openEditor(context, entry['id']?.toString() ?? '');
  }

  void _remove(String id) {
    _emit(_files().where((f) => f['id']?.toString() != id).toList());
  }

  Future<void> _openEditor(BuildContext context, String id) async {
    final files = _files();
    final index = files.indexWhere((f) => f['id']?.toString() == id);
    if (index < 0) return;
    final entry = Map<String, dynamic>.from(files[index]);
    final locale = Localizations.localeOf(context);
    final isEn = locale.languageCode == 'en';

    await Navigator.of(context).push<void>(
      MaterialPageRoute(
        builder: (_) => _PromptFileEditorPage(
          initialName: entry['name']?.toString() ?? '',
          initialPriority: _priorityOf(entry),
          initialBody: entry['body_md']?.toString() ??
              entry['body']?.toString() ??
              '',
          readOnly: readOnly,
          nameLabel: isEn ? 'Name' : 'Имя',
          priorityLabel: isEn ? 'Priority' : 'Приоритет',
          onCommit: (name, priority, body) {
            final next = _files();
            final i = next.indexWhere((f) => f['id']?.toString() == id);
            if (i < 0) return;
            next[i] = {
              ...next[i],
              'name': name,
              'priority': priority,
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
    final isEn = locale.languageCode == 'en';
    final files = _sorted(_files());
    final rows = [
      for (final f in files)
        AppEntityRow(
          id: f['id']?.toString() ?? f['name']?.toString() ?? '',
          title: f['name']?.toString() ?? '',
          cells: {'priority': '${_priorityOf(f)}'},
          trailing: readOnly
              ? null
              : IconButton(
                  icon: const Icon(Icons.delete_outline),
                  onPressed: () => _remove(f['id']?.toString() ?? ''),
                ),
        ),
    ];

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (!readOnly)
          AppInlineAddField(
            title: isEn ? 'Add prompt…' : 'Добавить промпт…',
            validator: (raw) => raw.trim().isNotEmpty,
            onSave: (raw) => _add(context, raw),
          ),
        SizedBox(
          height: (rows.length * 56.0).clamp(120, 420).toDouble(),
          child: AppEntityCollection(
            mode: AppEntityCollectionMode.table,
            rows: rows,
            primaryColumnLabel: isEn ? 'Name' : 'Имя',
            columns: [
              AppEntityColumn(
                id: 'priority',
                label: isEn ? 'Priority' : 'Приоритет',
                width: 100,
                align: AppEntityColumnAlign.center,
              ),
            ],
            onOpen: (row) => _openEditor(context, row.id),
            onDelete: readOnly
                ? null
                : (row) async => _remove(row.id),
            empty: EmptyPlaceholder(
              title: isEn ? 'No prompts' : 'Нет промптов',
              icon: Icons.description_outlined,
              fillViewport: false,
            ),
          ),
        ),
      ],
    );
  }
}

class _PromptFileEditorPage extends StatefulWidget {
  const _PromptFileEditorPage({
    required this.initialName,
    required this.initialPriority,
    required this.initialBody,
    required this.readOnly,
    required this.nameLabel,
    required this.priorityLabel,
    required this.onCommit,
  });

  final String initialName;
  final int initialPriority;
  final String initialBody;
  final bool readOnly;
  final String nameLabel;
  final String priorityLabel;
  final void Function(String name, int priority, String body) onCommit;

  @override
  State<_PromptFileEditorPage> createState() => _PromptFileEditorPageState();
}

class _PromptFileEditorPageState extends State<_PromptFileEditorPage> {
  late String _name;
  late int _priority;
  late String _body;
  final _bodyFocus = FocusNode();

  @override
  void initState() {
    super.initState();
    _name = widget.initialName;
    _priority = widget.initialPriority;
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
    widget.onCommit(name, _priority, _body);
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
              AppValuePreference<int>(
                title: widget.priorityLabel,
                value: _priority,
                icon: Icons.low_priority_outlined,
                enabled: !widget.readOnly,
                digitsOnly: true,
                keyboardType: TextInputType.number,
                inputToValue: (raw) => int.tryParse(raw.trim()),
                validateInput: (raw) => int.tryParse(raw.trim()) != null,
                onSave: (v) async {
                  setState(() => _priority = v);
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
                  onChanged: (v) => _body = v,
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
