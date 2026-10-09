import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/app_nav_preference.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/features/meta/meta_label.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Slot list for PC/server builds: one row per equipment type with matching build_scope.
class BuildSlotsField extends StatelessWidget {
  const BuildSlotsField({
    super.key,
    required this.seeds,
    required this.slots,
    required this.buildKind,
    required this.readOnly,
    required this.typesTable,
    required this.itemsTable,
    required this.offersTable,
    required this.pickView,
    required this.sectionTitle,
    required this.emptyLabel,
    required this.rowId,
    required this.onOpenPick,
  });

  final dynamic seeds;
  final Map<String, dynamic> slots;
  final String buildKind;
  final bool readOnly;
  final String typesTable;
  final String itemsTable;
  final String offersTable;
  final String pickView;
  final String sectionTitle;
  final String emptyLabel;
  final String? rowId;
  final void Function(String pickView, {String? rowId})? onOpenPick;

  @override
  Widget build(BuildContext context) {
    final types = _eligibleTypes();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (final type in types) _slotRow(context, type),
      ],
    );
  }

  Widget _slotRow(BuildContext context, _TypeSlot type) {
    final selected = _slotValue(type.id);
    final subtitle = selected == null
        ? Text(emptyLabel)
        : _selectedSubtitle(context, selected);
    return AppNavPreference(
      title: type.name,
      icon: Icons.memory_outlined,
      subtitle: subtitle,
      enabled: !readOnly && rowId != null && onOpenPick != null,
      onTap: () => _openSlotPick(type.id),
    );
  }

  /// Слот хранит найденную группу-кандидата (found_groups) или легаси item.
  String? _slotValue(String typeId) {
    final raw = slots[typeId]?.toString();
    if (raw == null || raw.isEmpty) return null;
    return raw;
  }

  Widget _selectedSubtitle(BuildContext context, String id) {
    final item = seeds.itemById(id);
    final body = item is Map && item['body'] is Map
        ? Map<String, dynamic>.from(item['body'] as Map)
        : <String, dynamic>{};
    final isGroup = body.containsKey('face_price') || body.containsKey('match_kind');
    if (isGroup) {
      final title = (body['face_title']?.toString() ?? '').trim();
      final pn = (body['part_number']?.toString() ?? '').trim();
      final price = body['face_price'];
      final alts = body['alternatives_count'];
      final offersCount = body['offers_count'];
      final parts = <String>[
        if (title.isNotEmpty) title else (pn.isNotEmpty ? pn : id),
        if (pn.isNotEmpty && title.isNotEmpty) pn,
        if (price != null) _fmtMoney(price),
        if (alts is num && alts > 0) '+$alts',
      ];
      // Без офферов у кандидата — warning (как «Нет оффера» в общем списке).
      final offers = offersCount is num
          ? offersCount.toInt()
          : int.tryParse(offersCount?.toString() ?? '') ?? 0;
      final color = offers > 0 ? null : context.appColors.warning;
      return Text(
        parts.join(' · '),
        maxLines: 2,
        overflow: TextOverflow.ellipsis,
        style: color == null
            ? null
            : Theme.of(context).textTheme.bodyMedium?.copyWith(color: color),
      );
    }
    final name = body['name']?.toString();
    return Text(
      (name != null && name.isNotEmpty) ? name : id,
      maxLines: 2,
      overflow: TextOverflow.ellipsis,
    );
  }

  String _fmtMoney(dynamic raw) {
    final v = raw is num ? raw.toDouble() : double.tryParse(raw.toString());
    if (v == null) return raw.toString();
    final s = v.toStringAsFixed(0);
    final buf = StringBuffer();
    for (var i = 0; i < s.length; i++) {
      if (i > 0 && (s.length - i) % 3 == 0) buf.write(' ');
      buf.write(s[i]);
    }
    return '${buf.toString()} ₽';
  }

  List<_TypeSlot> _eligibleTypes() {
    final items = seeds.itemsForTable(typesTable) as List? ?? const [];
    final out = <_TypeSlot>[];
    for (final item in items) {
      if (item is! Map) continue;
      final id = item['row_id']?.toString() ?? '';
      if (id.isEmpty) continue;
      final body = item['body'] is Map
          ? Map<String, dynamic>.from(item['body'] as Map)
          : <String, dynamic>{};
      if (!_scopeMatches(body)) continue;
      final name = body['name']?.toString() ?? id;
      final sortRaw = body['sort_order'];
      final sort = sortRaw is num
          ? sortRaw.toInt()
          : int.tryParse(sortRaw?.toString() ?? '') ?? 1000;
      out.add(_TypeSlot(id: id, name: name, sortOrder: sort));
    }
    out.sort((a, b) {
      final c = a.sortOrder.compareTo(b.sortOrder);
      if (c != 0) return c;
      return a.name.compareTo(b.name);
    });
    return out;
  }

  bool _scopeMatches(Map<String, dynamic> body) {
    // Missing/legacy → treat as all (show for any build_kind).
    final raw = body['build_scope']?.toString().trim();
    final scope = (raw == null || raw.isEmpty) ? 'all' : raw;
    // Legacy build_roles: ["pc","server"] ≈ all; ["server"] ≈ server.
    if (body['build_roles'] is List && (raw == null || raw.isEmpty)) {
      final roles = (body['build_roles'] as List).map((e) => e.toString()).toSet();
      if (roles.contains('pc') && roles.contains('server')) return true;
      if (buildKind.isEmpty) return true;
      return roles.contains(buildKind);
    }
    if (scope == 'all') return true;
    if (buildKind.isEmpty) return true;
    return scope == buildKind;
  }

  void _openSlotPick(String typeId) {
    if (rowId == null || onOpenPick == null) return;
    try {
      seeds.setPickContext(typeId: typeId, slotKey: typeId);
    } catch (_) {
      return;
    }
    onOpenPick!(pickView, rowId: rowId);
  }
}

class _TypeSlot {
  const _TypeSlot({
    required this.id,
    required this.name,
    required this.sortOrder,
  });

  final String id;
  final String name;
  final int sortOrder;
}

Map<String, dynamic> parseSlotsMap(dynamic raw) {
  if (raw is Map) return Map<String, dynamic>.from(raw);
  return <String, dynamic>{};
}

String buildSlotsSectionTitle(
  Map<String, dynamic>? fieldCfg,
  AppLocalizations l10n,
  Locale locale,
) {
  final raw = fieldCfg?['section_title'];
  final resolved = resolveMetaLabel(raw, l10n, locale: locale);
  if (resolved.isNotEmpty) return resolved;
  return '';
}

String buildSlotsEmptyLabel(
  Map<String, dynamic>? fieldCfg,
  AppLocalizations l10n,
  Locale locale,
) {
  final raw = fieldCfg?['empty_label'];
  final resolved = resolveMetaLabel(raw, l10n, locale: locale);
  if (resolved.isNotEmpty) return resolved;
  return locale.languageCode == 'en' ? 'Not selected' : 'Не выбран';
}
