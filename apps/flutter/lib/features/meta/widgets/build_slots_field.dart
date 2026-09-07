import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/app_nav_preference.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/features/meta/meta_label.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Slot list for PC/server builds: one row per equipment type with matching build_roles.
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
        if (sectionTitle.isNotEmpty)
          Padding(
            padding: const EdgeInsets.fromLTRB(
              AppSpacing.md,
              AppSpacing.sm,
              AppSpacing.md,
              AppSpacing.xs,
            ),
            child: Text(
              sectionTitle,
              style: Theme.of(context).textTheme.titleSmall,
            ),
          ),
        for (final type in types)
          AppNavPreference(
            title: type.name,
            icon: Icons.memory_outlined,
            subtitle: Text(_slotSubtitle(type.id)),
            enabled: !readOnly && rowId != null && onOpenPick != null,
            onTap: () => _openSlotPick(type.id),
          ),
      ],
    );
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
      final roles = body['build_roles'];
      final roleList = roles is List
          ? roles.map((e) => e.toString()).toList()
          : <String>[];
      if (buildKind.isNotEmpty && !roleList.contains(buildKind)) continue;
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

  String _slotSubtitle(String typeId) {
    final itemId = slots[typeId]?.toString();
    if (itemId == null || itemId.isEmpty) return emptyLabel;
    final item = seeds.itemById(itemId);
    final body = item is Map && item['body'] is Map
        ? Map<String, dynamic>.from(item['body'] as Map)
        : <String, dynamic>{};
    final name = body['name']?.toString();
    if (name != null && name.isNotEmpty) return name;
    return itemId;
  }

  Future<void> _openSlotPick(String typeId) async {
    if (rowId == null || onOpenPick == null) return;
    final ctxItem = seeds.itemById(rowId!);
    if (ctxItem is! Map) return;
    final body = Map<String, dynamic>.from(
      ctxItem['body'] is Map
          ? Map<String, dynamic>.from(ctxItem['body'] as Map)
          : <String, dynamic>{},
    );
    body['_pick_type_id'] = typeId;
    body['_slot_key'] = typeId;
    final upsert = seeds.upsertBody(rowId!, body);
    if (upsert is Future) await upsert;
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
  return locale.languageCode == 'en' ? 'Components' : 'Комплектующие';
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
