import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/app_choice_preference.dart';

/// «Состав» готовой сборки: контракт характеристик по слотам. Каждое поле —
/// выбор из групп типа комплектующего (или канонического списка); изменение
/// пишет `constraints` слота (ready_build_slots) и, значит, меняет подбор
/// карточек. Никакого свободного текста: значения только из групп.
///
/// PC/сервер — канонический контракт (сокет, DDR, ОЗУ, диски, БП, GPU,
/// форм-фактор, водянка). Устройства-комплекты (ноутбук/телефон/…) — редактор
/// ограничений единственного слота по шаблону типа.
class BuildSpecEditor extends StatelessWidget {
  const BuildSpecEditor({
    super.key,
    required this.seeds,
    required this.buildId,
    required this.slotsTable,
    required this.typesTable,
    required this.readOnly,
    required this.buildKind,
  });

  final dynamic seeds;
  final String? buildId;
  final String slotsTable;
  final String typesTable;
  final bool readOnly;
  final String buildKind;

  // Канонический контракт PC/сервера: поле → слот/ограничение/источник выбора.
  static const _pcFields = <_SpecField>[
    _SpecField(key: 'socket', label: 'Сокет', slotType: 'etype_cpu', constraintKey: 'socket', choicesFrom: ('etype_cpu', 'socket')),
    _SpecField(key: 'ram_type', label: 'DDR, версия', slotType: 'etype_ram', constraintKey: 'ram_type', choicesFrom: ('etype_ram', 'ram_type')),
    _SpecField(key: 'ram_total', label: 'RAM, гб', slotType: 'etype_ram', constraintKey: 'ram_total', choices: ['8', '16', '32', '64', '128']),
    _SpecField(key: 'ssd_total', label: 'SSD, гб', slotType: 'etype_storage', constraintKey: 'storage_total', extraConstraints: {'drive_type': 'SSD'}, choices: ['128', '256', '512', '1024', '2048']),
    _SpecField(key: 'hdd_total', label: 'HDD, гб', slotType: 'etype_storage', constraintKey: 'storage_total', extraConstraints: {'drive_type': 'HDD'}, choices: ['256', '512', '1024', '2048', '4096', '8192'], optional: true),
    _SpecField(key: 'wattage', label: 'Питание, вт', slotType: 'etype_psu', constraintKey: 'wattage', choicesFrom: ('etype_psu', 'wattage')),
    _SpecField(key: 'vram', label: 'GPU, гб', slotType: 'etype_gpu', constraintKey: 'vram', choicesFrom: ('etype_gpu', 'vram'), optional: true),
    _SpecField(key: 'form_factor', label: 'Формфактор', slotType: 'etype_motherboard', constraintKey: 'form_factor', choicesFrom: ('etype_motherboard', 'form_factor'), optional: true),
    _SpecField(key: 'cooling_kind', label: 'Водянка, да-нет', slotType: 'etype_cooling', constraintKey: 'cooling_kind', labeledChoices: [('air', 'Нет'), ('aio', 'Да')], defaultValue: 'air'),
  ];

  // Базовый набор слотов PC: всегда есть корпус и охлаждение (по требованию
  // заказчика не зависят от полей формы).
  static const _baseSlotTypes = [
    'etype_cpu', 'etype_motherboard', 'etype_ram', 'etype_storage',
    'etype_psu', 'etype_case', 'etype_cooling',
  ];

  @override
  Widget build(BuildContext context) {
    if (buildId == null || buildId!.isEmpty) return const SizedBox.shrink();
    if (buildKind == 'pc' || buildKind == 'server') {
      _ensureBaseSlots();
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          for (final f in _pcFields) _field(context, f),
        ],
      );
    }
    // устройство-комплект: один слот, редактор ограничений по шаблону типа
    return _DeviceSpec(
      seeds: seeds,
      buildId: buildId!,
      slotsTable: slotsTable,
      typesTable: typesTable,
      readOnly: readOnly,
      buildKind: buildKind,
      onChanged: _patchSlotConstraints,
    );
  }

  // ------------------------------------------------------------------ поля PC

  Widget _field(BuildContext context, _SpecField f) {
    final slot = _slotFor(f);
    final constraints = slot != null
        ? (slot['constraints'] is Map
            ? Map<String, dynamic>.from(slot['constraints'] as Map)
            : <String, dynamic>{})
        : <String, dynamic>{};
    final current = constraints[f.constraintKey]?.toString() ?? '';
    final choices = _choicesFor(f);
    final currentEntry = choices.firstWhere(
      (c) => c.key == current,
      orElse: () => MapEntry(current, current.isEmpty ? '—' : current),
    );
    final known = choices.any((c) => c.key == current);
    final allChoices = known || current.isEmpty
        ? choices
        : [...choices, MapEntry(current, current)];
    return AppChoicePreference<MapEntry<String, String>>(
      title: f.label,
      value: currentEntry,
      choices: allChoices,
      keyFor: (e) => e.key,
      labelFor: (e) => e.value,
      enabled: !readOnly,
      onSave: (e) => _applyField(f, e.key),
    );
  }

  List<MapEntry<String, String>> _choicesFor(_SpecField f) {
    if (f.labeledChoices != null) {
      return [for (final e in f.labeledChoices!) MapEntry(e.$1, e.$2)];
    }
    if (f.choices != null) {
      return [for (final v in f.choices!) MapEntry(v, v)];
    }
    if (f.choicesFrom != null) {
      final (typeId, fieldKey) = f.choicesFrom!;
      final typeRow = _typeRow(typeId);
      final body = typeRow?['body'];
      final fields = (body is Map ? body['fields_json'] : null);
      if (fields is List) {
        for (final field in fields) {
          if (field is! Map || field['key']?.toString() != fieldKey) continue;
          final kind = field['kind']?.toString() ?? 'text';
          final out = <MapEntry<String, String>>[];
          if (kind == 'enum') {
            for (final v in (field['values'] as List? ?? const [])) {
              final s = v?.toString() ?? '';
              if (s.isNotEmpty) out.add(MapEntry(s, s));
            }
          } else if (kind == 'groups') {
            for (final g in (field['groups'] as List? ?? const [])) {
              if (g is Map) {
                final v = g['value']?.toString() ?? '';
                if (v.isEmpty) continue;
                final l = g['label']?.toString() ?? '';
                out.add(MapEntry(v, l.isEmpty ? v : l));
              }
            }
          }
          if (out.isNotEmpty) return out;
        }
      }
    }
    return const [];
  }

  Map<String, dynamic>? _typeRow(String typeId) {
    for (final item in seeds.itemsForTable(typesTable) as List) {
      if (item is Map && item['row_id']?.toString() == typeId) {
        return Map<String, dynamic>.from(item);
      }
    }
    return null;
  }

  // --------------------------------------------------------------- запись слотов

  List<Map<String, dynamic>> _slots() {
    final out = <Map<String, dynamic>>[];
    for (final item in seeds.itemsForTable(slotsTable) as List) {
      if (item is! Map) continue;
      final body = item['body'];
      if (body is! Map) continue;
      if (body['build_id']?.toString() != buildId) continue;
      out.add({'row_id': item['row_id'], ...Map<String, dynamic>.from(body)});
    }
    return out;
  }

  Map<String, dynamic>? _slotFor(_SpecField f) {
    for (final slot in _slots()) {
      if (slot['type_id']?.toString() != f.slotType) continue;
      final extra = f.extraConstraints;
      if (extra != null) {
        final constraints = slot['constraints'] is Map
            ? Map<String, dynamic>.from(slot['constraints'] as Map)
            : <String, dynamic>{};
        var ok = true;
        for (final e in extra.entries) {
          if (constraints[e.key]?.toString() != e.value) ok = false;
        }
        if (!ok) continue;
      }
      return slot;
    }
    return null;
  }

  Future<void> _applyField(_SpecField f, String value) async {
    if (readOnly) return;
    final slot = _slotFor(f);
    final extra = f.extraConstraints ?? const <String, String>{};
    if (slot != null) {
      final slotId = slot['row_id']?.toString() ?? '';
      final constraints = slot['constraints'] is Map
          ? Map<String, dynamic>.from(slot['constraints'] as Map)
          : <String, dynamic>{};
      if (value.isEmpty) {
        constraints.remove(f.constraintKey);
        // очистка необязательного слота (HDD/GPU) — удаляем слот целиком
        if (f.optional && constraints.values.every((v) => v.toString().isEmpty)) {
          final del = seeds.deleteRow(slotId);
          if (del is Future) await del;
          return;
        }
      } else {
        constraints[f.constraintKey] = value;
        for (final e in extra.entries) {
          constraints[e.key] = e.value;
        }
      }
      await _patchSlotConstraints(slotId, constraints);
      return;
    }
    if (value.isEmpty) return;
    // слота нет — создаём (SSD/HDD/GPU и любые недостающие базовые)
    final constraints = <String, dynamic>{f.constraintKey: value, ...extra};
    await _createSlot(f.slotType, constraints);
  }

  Future<void> _patchSlotConstraints(String slotId, Map<String, dynamic> constraints) async {
    final patch = seeds.patchField(slotId, 'constraints', constraints);
    if (patch is Future) await patch;
  }

  Future<void> _createSlot(String typeId, Map<String, dynamic> constraints) async {
    final created = seeds.createRow(slotsTable);
    final slotId = created is Future ? await created as String : created as String;
    final body = seeds.bodyFor(slotId);
    body['build_id'] = buildId;
    body['type_id'] = typeId;
    body['constraints'] = constraints;
    body['qty'] = 1;
    final upsert = seeds.upsertBody(slotId, body);
    if (upsert is Future) await upsert;
  }

  void _ensureBaseSlots() {
    if (readOnly || buildId == null || buildId!.isEmpty) return;
    final existing = _slots().map((s) => s['type_id']?.toString() ?? '').toSet();
    for (final typeId in _baseSlotTypes) {
      if (existing.contains(typeId)) continue;
      // корпус/кулер/… создаются пустыми — их заполняет выбор в форме или
      // автоподбор; важно, что слот существует (сборка всегда с ними)
      _createSlot(typeId, const {});
    }
  }
}

class _SpecField {
  const _SpecField({
    required this.key,
    required this.label,
    required this.slotType,
    required this.constraintKey,
    this.choicesFrom,
    this.choices,
    this.labeledChoices,
    this.extraConstraints,
    this.optional = false,
    this.defaultValue,
  });

  final String key;
  final String label;
  final String slotType;
  final String constraintKey;
  final (String, String)? choicesFrom;
  final List<String>? choices;
  final List<(String, String)>? labeledChoices;
  final Map<String, String>? extraConstraints;
  final bool optional;
  final String? defaultValue;
}

/// Устройство-комплект (ноутбук/неттоп/моноблок/телефон): один слот, редактор
/// ограничений по шаблону типа устройства.
class _DeviceSpec extends StatelessWidget {
  const _DeviceSpec({
    required this.seeds,
    required this.buildId,
    required this.slotsTable,
    required this.typesTable,
    required this.readOnly,
    required this.buildKind,
    required this.onChanged,
  });

  final dynamic seeds;
  final String buildId;
  final String slotsTable;
  final String typesTable;
  final bool readOnly;
  final String buildKind;
  final Future<void> Function(String slotId, Map<String, dynamic> constraints) onChanged;

  String get _deviceType => {
        'laptop': 'etype_laptop',
        'nettop': 'etype_nettop',
        'monoblock': 'etype_monoblock',
        'phone': 'etype_phone',
      }[buildKind] ??
      '';

  @override
  Widget build(BuildContext context) {
    final typeRow = _typeRow(_deviceType);
    final fields = (typeRow?['body'] is Map)
        ? ((typeRow!['body'] as Map)['fields_json'] as List? ?? const [])
        : const [];
    final slot = _deviceSlot();
    final constraints = slot != null
        ? (slot['constraints'] is Map
            ? Map<String, dynamic>.from(slot['constraints'] as Map)
            : <String, dynamic>{})
        : <String, dynamic>{};
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (final field in fields.whereType<Map>())
          _deviceField(context, field, slot, constraints),
      ],
    );
  }

  Map<String, dynamic>? _typeRow(String typeId) {
    for (final item in seeds.itemsForTable(typesTable) as List) {
      if (item is Map && item['row_id']?.toString() == typeId) {
        return Map<String, dynamic>.from(item);
      }
    }
    return null;
  }

  Map<String, dynamic>? _deviceSlot() {
    for (final item in seeds.itemsForTable(slotsTable) as List) {
      if (item is! Map) continue;
      final body = item['body'];
      if (body is! Map) continue;
      if (body['build_id']?.toString() != buildId) continue;
      if (body['type_id']?.toString() == _deviceType) {
        return {'row_id': item['row_id'], ...Map<String, dynamic>.from(body)};
      }
    }
    return null;
  }

  Widget _deviceField(
    BuildContext context,
    Map field,
    Map<String, dynamic>? slot,
    Map<String, dynamic> constraints,
  ) {
    final key = field['key']?.toString() ?? '';
    if (key.isEmpty) return const SizedBox.shrink();
    final labelRaw = field['label'];
    final label = labelRaw is Map
        ? (labelRaw['ru']?.toString() ?? labelRaw['en']?.toString() ?? key)
        : (labelRaw?.toString() ?? key);
    final required = field['required'] == true;
    final title = required ? '$label *' : label;
    final current = constraints[key]?.toString() ?? '';
    final choices = <MapEntry<String, String>>[];
    final kind = field['kind']?.toString() ?? 'text';
    if (kind == 'enum') {
      for (final v in (field['values'] as List? ?? const [])) {
        final s = v?.toString() ?? '';
        if (s.isNotEmpty) choices.add(MapEntry(s, s));
      }
    } else if (kind == 'groups') {
      for (final g in (field['groups'] as List? ?? const [])) {
        if (g is Map) {
          final v = g['value']?.toString() ?? '';
          if (v.isEmpty) continue;
          final l = g['label']?.toString() ?? '';
          choices.add(MapEntry(v, l.isEmpty ? v : l));
        }
      }
    }
    if (choices.isEmpty) return const SizedBox.shrink();
    final currentEntry = choices.firstWhere(
      (c) => c.key == current,
      orElse: () => MapEntry(current, current.isEmpty ? '—' : current),
    );
    return AppChoicePreference<MapEntry<String, String>>(
      title: title,
      value: currentEntry,
      choices: choices,
      keyFor: (e) => e.key,
      labelFor: (e) => e.value,
      enabled: !readOnly,
      onSave: (e) async {
        if (slot == null) return; // слот создаётся при attach/агентом
        final next = Map<String, dynamic>.from(constraints);
        if (e.key.isEmpty) {
          next.remove(key);
        } else {
          next[key] = e.key;
        }
        await onChanged(slot['row_id']?.toString() ?? '', next);
      },
    );
  }
}
