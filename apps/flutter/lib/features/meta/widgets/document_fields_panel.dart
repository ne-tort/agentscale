import 'dart:async';

import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/features/meta/meta_label.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Панель реквизитов документов (КП/Спецификация) — singleton-строка таблицы
/// `document_fields` активного чата. Ставится рядом со сводкой бюджета:
/// то, что нельзя вывести из данных (стороны, номера договоров, сроки),
/// вводится здесь и подставляется в шаблоны при экспорте.
class DocumentFieldsPanel extends StatefulWidget {
  const DocumentFieldsPanel({
    super.key,
    required this.tableSlug,
    required this.title,
    required this.fields,
    required this.labels,
    required this.itemsForTable,
    required this.itemById,
    required this.createRow,
    required this.upsertBody,
  });

  final String tableSlug;
  final Object? title;
  final List<String> fields;

  /// column → label (resolved from the module meta).
  final Map<String, String> labels;
  final List<Map<String, dynamic>> Function(String tableSlug) itemsForTable;
  final Map<String, dynamic>? Function(String rowId) itemById;
  final Future<String> Function(String tableSlug) createRow;
  final Future<void> Function(String rowId, Map<String, dynamic> body) upsertBody;

  @override
  State<DocumentFieldsPanel> createState() => _DocumentFieldsPanelState();
}

class _DocumentFieldsPanelState extends State<DocumentFieldsPanel> {
  String? _rowId;
  final Map<String, TextEditingController> _controllers = {};
  Timer? _saveTimer;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  void _load() {
    final items = widget.itemsForTable(widget.tableSlug);
    final row = items.isEmpty ? null : items.first;
    _rowId = row?['row_id'] as String?;
    final body = row?['body'] is Map
        ? Map<String, dynamic>.from(row!['body'] as Map)
        : const <String, dynamic>{};
    for (final field in widget.fields) {
      _controllers[field] =
          TextEditingController(text: body[field]?.toString() ?? '');
    }
    _loading = false;
  }

  @override
  void dispose() {
    _saveTimer?.cancel();
    for (final c in _controllers.values) {
      c.dispose();
    }
    super.dispose();
  }

  void _scheduleSave() {
    _saveTimer?.cancel();
    _saveTimer = Timer(const Duration(milliseconds: 600), () async {
      final body = <String, dynamic>{
        for (final field in widget.fields)
          field: _numField(field) ? _numOrNull(field) : _controllers[field]?.text,
      };
      try {
        if (_rowId == null) {
          _rowId = await widget.createRow(widget.tableSlug);
        }
        await widget.upsertBody(_rowId!, body);
      } catch (_) {
        // best-effort: реквизиты не должны ронять экран
      }
    });
  }

  bool _numField(String field) => field == 'kp_valid_days';

  Object? _numOrNull(String field) {
    final raw = _controllers[field]?.text.trim() ?? '';
    if (raw.isEmpty) return null;
    return num.tryParse(raw);
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final colors = context.appColors;
    final scheme = Theme.of(context).colorScheme;
    if (_loading) return const SizedBox.shrink();
    return Container(
      padding: const EdgeInsets.all(AppSpacing.sm),
      decoration: BoxDecoration(
        color: colors.surface,
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: colors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(
            resolveMetaLabel(widget.title, l10n),
            style: Theme.of(context).textTheme.titleSmall?.copyWith(
                  fontWeight: FontWeight.w600,
                ),
          ),
          const SizedBox(height: AppSpacing.xs),
          Flexible(
            child: SingleChildScrollView(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  for (final field in widget.fields)
                    Padding(
                      padding: const EdgeInsets.only(bottom: AppSpacing.xs),
                      child: TextField(
                        controller: _controllers[field],
                        style: Theme.of(context).textTheme.bodySmall,
                        decoration: InputDecoration(
                          isDense: true,
                          labelText: widget.labels[field] ?? field,
                          labelStyle: TextStyle(
                            fontSize: 11,
                            color: scheme.onSurfaceVariant,
                          ),
                          border: const OutlineInputBorder(),
                          contentPadding: const EdgeInsets.symmetric(
                            horizontal: 8,
                            vertical: 8,
                          ),
                        ),
                        keyboardType: _numField(field)
                            ? TextInputType.number
                            : TextInputType.text,
                        onChanged: (_) => _scheduleSave(),
                      ),
                    ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}
