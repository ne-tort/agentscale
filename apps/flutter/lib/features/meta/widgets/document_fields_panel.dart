import 'dart:async';

import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/features/meta/meta_label.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Панель реквизитов документов (КП/Спецификация) в «Бюджетировании»:
/// две карточки в одну линию — «Поставщик» и «Сделка» (без общего заголовка).
///
/// Две группы singleton-строк:
/// - компания (`document_company_fields`, chats=all — весь кабинет): наша
///   сторона, город, приложение, сроки; значения сразу подставлены дефолтами
///   колонок (из шаблона) и живут персистентно;
/// - сделка (`document_fields`, chats=current): покупатель, номера договора/
///   спецификации (автогенерация «0001» из seq-счётчиков компании), дата
///   договора (по умолчанию сегодняшняя), адреса.
class DocumentFieldsPanel extends StatefulWidget {
  const DocumentFieldsPanel({
    super.key,
    required this.companyTable,
    required this.dealTable,
    required this.companyTitle,
    required this.dealTitle,
    required this.companyFields,
    required this.dealFields,
    required this.labels,
    required this.defaults,
    required this.itemsForTable,
    required this.createRow,
    required this.upsertBody,
  });

  final String companyTable;
  final String dealTable;
  final Object? companyTitle;
  final Object? dealTitle;

  /// column names (company group).
  final List<String> companyFields;

  /// {column, auto?} entries (deal group); auto = seq-колонка компании.
  final List<Map<String, dynamic>> dealFields;
  final Map<String, String> labels;
  final Map<String, Object?> defaults;
  final List<Map<String, dynamic>> Function(String tableSlug) itemsForTable;
  final Future<String> Function(String tableSlug) createRow;
  final Future<void> Function(String rowId, Map<String, dynamic> body) upsertBody;

  @override
  State<DocumentFieldsPanel> createState() => _DocumentFieldsPanelState();
}

class _DocumentFieldsPanelState extends State<DocumentFieldsPanel> {
  String? _companyId;
  String? _dealId;
  final Map<String, TextEditingController> _controllers = {};
  Timer? _saveTimer;

  // какие авто-номера мы присвоили из seq (для инкремента при сохранении)
  String? _assignedContract;
  String? _assignedSpec;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Map<String, dynamic> _bodyOf(String table) {
    final items = widget.itemsForTable(table);
    if (items.isEmpty) return {};
    final body = items.first['body'];
    return body is Map ? Map<String, dynamic>.from(body) : {};
  }

  void _load() {
    final company = _bodyOf(widget.companyTable);
    final deal = _bodyOf(widget.dealTable);
    _companyId = company.isEmpty ? null : widget.itemsForTable(widget.companyTable).first['row_id'] as String?;
    _dealId = deal.isEmpty ? null : widget.itemsForTable(widget.dealTable).first['row_id'] as String?;

    for (final f in widget.companyFields) {
      _controllers[f] = TextEditingController(
        text: (company[f] ?? widget.defaults[f] ?? '').toString(),
      );
    }
    final contractSeq = _asInt(company['contract_seq']) ?? 0;
    final specSeq = _asInt(company['spec_seq']) ?? 0;
    for (final entry in widget.dealFields) {
      final f = entry['column']?.toString() ?? '';
      if (f.isEmpty) continue;
      var initial = (deal[f] ?? widget.defaults[f] ?? '').toString();
      if (initial.isEmpty && entry['auto'] == 'contract_seq') {
        initial = _seqLabel(contractSeq + 1);
        _assignedContract = initial;
      } else if (initial.isEmpty && entry['auto'] == 'spec_seq') {
        initial = _seqLabel(specSeq + 1);
        _assignedSpec = initial;
      } else if (initial.isEmpty && entry['auto'] == 'today') {
        // дата договора по умолчанию — сегодня (формат рендера документов)
        initial = _todayRu();
      }
      _controllers[f] = TextEditingController(text: initial);
    }
  }

  static const _monthsGenRu = [
    'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
  ];

  /// «DD» месяца YYYY г. — 1:1 с fmt_date_ru из equipment_docs_render.
  String _todayRu() {
    final now = DateTime.now();
    final day = now.day.toString().padLeft(2, '0');
    return '«$day» ${_monthsGenRu[now.month - 1]} ${now.year} г.';
  }

  String _seqLabel(int n) => n.toString().padLeft(4, '0');

  int? _asInt(Object? raw) {
    if (raw is num) return raw.toInt();
    if (raw is String) return int.tryParse(raw.trim());
    return null;
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
    _saveTimer = Timer(const Duration(milliseconds: 600), _save);
  }

  Future<void> _save() async {
    try {
      final loadedCompany = _bodyOf(widget.companyTable);
      final companyBody = <String, dynamic>{
        for (final f in widget.companyFields) f: _value(f),
      };
      // авто-номера заняли seq: счётчик компании догоняем до выданного номера
      for (final pair in [
        ('contract_seq', _assignedContract),
        ('spec_seq', _assignedSpec),
      ]) {
        final field = pair.$1;
        final assigned = pair.$2;
        if (assigned == null) continue;
        final current = _asInt(loadedCompany[field]) ?? 0;
        final used = _asInt(assigned) ?? 0;
        if (used > current) companyBody[field] = used;
      }
      _companyId ??= await widget.createRow(widget.companyTable);
      await widget.upsertBody(_companyId!, companyBody);

      final dealBody = <String, dynamic>{};
      for (final entry in widget.dealFields) {
        final f = entry['column']?.toString() ?? '';
        if (f.isEmpty) continue;
        dealBody[f] = _value(f);
      }
      _dealId ??= await widget.createRow(widget.dealTable);
      await widget.upsertBody(_dealId!, dealBody);
    } catch (_) {
      // реквизиты не должны ронять экран
    }
  }

  Object? _value(String field) {
    final raw = _controllers[field]?.text ?? '';
    if (field == 'kp_valid_days') {
      return raw.isEmpty ? null : num.tryParse(raw);
    }
    return raw;
  }

  Widget _field(String column) {
    final scheme = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.only(bottom: AppSpacing.xs),
      child: TextField(
        controller: _controllers[column],
        style: Theme.of(context).textTheme.bodySmall,
        decoration: InputDecoration(
          isDense: true,
          labelText: widget.labels[column] ?? column,
          labelStyle: TextStyle(fontSize: 11, color: scheme.onSurfaceVariant),
          border: const OutlineInputBorder(),
          contentPadding: const EdgeInsets.symmetric(horizontal: 8, vertical: 8),
        ),
        keyboardType: column == 'kp_valid_days' ? TextInputType.number : TextInputType.text,
        onChanged: (_) => _scheduleSave(),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    // две независимые карточки в одну линию (без общего заголовка):
    // «Поставщик» и «Сделка» — в ряду со сводкой бюджета их строит
    // CollectionViewInterpreter.
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Expanded(
          child: _card(
            widget.companyTitle,
            widget.companyFields.map(_field).toList(),
          ),
        ),
        const SizedBox(width: AppSpacing.sm),
        Expanded(
          child: _card(
            widget.dealTitle,
            [
              for (final entry in widget.dealFields)
                _field(entry['column']?.toString() ?? ''),
            ],
          ),
        ),
      ],
    );
  }

  Widget _card(Object? title, List<Widget> children) {
    final l10n = AppLocalizations.of(context);
    final colors = context.appColors;
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
          Padding(
            padding: const EdgeInsets.only(bottom: AppSpacing.xs / 2),
            child: Text(
              resolveMetaLabel(title, l10n),
              style: Theme.of(context).textTheme.labelMedium?.copyWith(
                    fontWeight: FontWeight.w600,
                    color: colors.muted,
                  ),
            ),
          ),
          ...children,
        ],
      ),
    );
  }
}
