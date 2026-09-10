import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/app_value_preference.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_icon_button.dart';
import 'package:prodavan/core/widgets/app_inline_add_field.dart';
import 'package:prodavan/core/widgets/app_snack_bar.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/meta/meta_icon.dart';
import 'package:prodavan/features/meta/meta_label.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/features/meta/preview/preview_stub.dart';
import 'package:prodavan/features/meta/runtime/cabinet_data_controller.dart';
import 'package:prodavan/features/meta/runtime/owner_module_data_controller.dart';
import 'package:prodavan/features/meta/runtime/runtime_data_adapter.dart';
import 'package:prodavan/features/meta/widgets/project_multiselect_field.dart';
import 'package:prodavan/l10n/app_localizations.dart';

class CollectionViewInterpreter extends StatelessWidget {
  const CollectionViewInterpreter({
    super.key,
    required this.manifest,
    required this.view,
    required this.seeds,
    this.onOpenForm,
    this.readOnly = false,
    this.contextRowId,
  });

  final ModuleMetaManifest manifest;
  final Map<String, dynamic> view;
  final dynamic seeds;
  final void Function(String viewSlug, {String? rowId})? onOpenForm;
  final bool readOnly;
  final String? contextRowId;

  bool _hasInlineAdd(Map<String, dynamic> uiJson) {
    final inline = uiJson['inline_add'];
    return !readOnly && inline is Map;
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final ui = view['ui_json'];
    if (ui is! Map) {
      return EmptyPlaceholder(title: l10n.adminMetaInvalid);
    }
    final uiJson = Map<String, dynamic>.from(ui);
    final tableSlug = view['table_slug'] as String? ?? '';
    if (tableSlug.isEmpty) {
      return EmptyPlaceholder(title: l10n.adminMetaInvalid);
    }

    return ListenableBuilder(
      listenable: seeds is Listenable ? seeds as Listenable : ValueNotifier(0),
      builder: (context, _) {
        final locale = Localizations.localeOf(context);
        final titleField = uiJson['title_field'] as String? ?? 'name';
        final allColumns = _columns(uiJson, l10n, locale);
        final primaryLabel = allColumns
                .where((c) => c.id == titleField)
                .map((c) => c.label)
                .cast<String?>()
                .firstWhere((_) => true, orElse: () => null) ??
            (allColumns.isNotEmpty ? allColumns.first.label : l10n.commonEntity);
        // Primary column already shows title_field — do not repeat it as a data column.
        final columns = allColumns.where((c) => c.id != titleField).toList();
        final rawRows = _filteredRows(seeds, tableSlug, uiJson);
        final styled = _applyRowStyles(context, uiJson, rawRows);
        final rows = _withSelection(context, uiJson, styled);
        final hasInline = _hasInlineAdd(uiJson);
        final toolbar = _toolbar(context, uiJson, tableSlug, l10n, skipCreate: hasInline);
        final emptyUi = uiJson['empty'];
        final emptyTitle = emptyUi is Map && emptyUi['title'] != null
            ? resolveMetaLabel(emptyUi['title'], l10n, locale: locale)
            : l10n.adminModuleSeedEmpty;
        final emptyIconName = emptyUi is Map ? emptyUi['icon'] as String? : null;
        final emptyIcon = metaIconFromName(
          emptyIconName,
          fallback: Icons.inbox_outlined,
        );
        final createLabel = hasInline ? null : _createLabel(uiJson, l10n, locale);

        final collection = AppEntityCollection(
          rows: rows,
          columns: columns,
          primaryColumnLabel: primaryLabel,
          toolbar: toolbar,
          empty: EmptyPlaceholder(
            title: emptyTitle.isEmpty ? l10n.adminModuleSeedEmpty : emptyTitle,
            icon: emptyIcon,
            fillViewport: false,
            action: !readOnly && createLabel != null
                ? TextButton(
                    onPressed: () => _create(context, uiJson, tableSlug),
                    child: Text(createLabel),
                  )
                : null,
          ),
          onOpen: (row) {
            final selection = uiJson['selection'];
            if (selection is Map && selection['disable_row_tap'] == true) {
              return;
            }
            final rowTap = uiJson['row_tap'];
            if (rowTap is Map && onOpenForm != null) {
              final kind = rowTap['kind'] as String?;
              final targetView = rowTap['view'] as String?;
              if (targetView != null &&
                  (kind == 'open_form' || kind == 'open_view')) {
                onOpenForm!(targetView, rowId: row.id);
              }
            }
          },
          onDelete: readOnly
              ? null
              : (row) async {
                  try {
                    final delete = seeds.deleteRow(row.id);
                    if (delete is Future) await delete;
                  } catch (e) {
                    if (context.mounted) AppErrors.showSnack(context, e);
                    rethrow;
                  }
                },
        );

        if (!hasInline && !_hasContextHeader(uiJson)) return collection;

        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (_hasContextHeader(uiJson))
              _CollectionContextHeader(
                headerConfig: Map<String, dynamic>.from(uiJson['context_header'] as Map),
                seeds: seeds,
                manifest: manifest,
                contextRowId: contextRowId,
                readOnly: readOnly,
              ),
            if (hasInline)
              _CollectionInlineAddHost(
                inlineConfig: Map<String, dynamic>.from(uiJson['inline_add'] as Map),
                tableSlug: tableSlug,
                uiJson: uiJson,
                seeds: seeds,
                contextRowId: contextRowId,
              ),
            Expanded(child: collection),
          ],
        );
      },
    );
  }

  bool _hasContextHeader(Map<String, dynamic> uiJson) {
    final header = uiJson['context_header'];
    return header is Map &&
        contextRowId != null &&
        contextRowId!.isNotEmpty;
  }

  List<AppEntityRow> _applyRowStyles(
    BuildContext context,
    Map<String, dynamic> uiJson,
    List<AppEntityRow> rows,
  ) {
    final styleRules = uiJson['row_style'];
    if (styleRules is! List || styleRules.isEmpty) return rows;
    final colors = context.appColors;
    return rows.map((row) {
      final item = seeds.itemById(row.id);
      final body = item is Map && item['body'] is Map
          ? Map<String, dynamic>.from(item['body'] as Map)
          : <String, dynamic>{};
      Color? accent;
      for (final rule in styleRules.whereType<Map>()) {
        final when = rule['when'];
        if (when is! Map) continue;
        if (!_bodyMatchesWhen(body, Map<String, dynamic>.from(when))) continue;
        final kind = rule['accent']?.toString();
        if (kind == 'error') {
          accent = colors.danger;
          break;
        }
        if (kind == 'warning') {
          accent = colors.warning;
          break;
        }
      }
      if (accent == null) return row;
      return AppEntityRow(
        id: row.id,
        title: row.title,
        subtitle: row.subtitle,
        cells: row.cells,
        cellWidgets: row.cellWidgets,
        leading: row.leading,
        trailing: row.trailing,
        titleColor: row.titleColor,
        rowColor: accent,
        titleBold: row.titleBold,
      );
    }).toList();
  }

  bool _bodyMatchesWhen(Map<String, dynamic> body, Map<String, dynamic> when) {
    final field = when['field']?.toString();
    if (field == null || field.isEmpty) return true;
    final actual = body[field];
    if (when.containsKey('eq')) {
      final expected = when['eq'];
      if (expected is bool) return (actual == true) == expected;
      return actual?.toString() == expected?.toString();
    }
    if (when['in'] is List) {
      final allowed = (when['in'] as List).map((e) => e.toString()).toSet();
      return actual != null && allowed.contains(actual.toString());
    }
    return true;
  }

  List<AppEntityRow> _filteredRows(dynamic seeds, String tableSlug, Map<String, dynamic> uiJson) {
    var all = seeds.entityRows(tableSlug, uiJson) as List<AppEntityRow>;
    if (tableSlug == 'equipment_types') {
      all = [...all]..sort((a, b) {
          final ba = seeds.itemById(a.id);
          final bb = seeds.itemById(b.id);
          final oa = _sortOrderOf(ba);
          final ob = _sortOrderOf(bb);
          final c = oa.compareTo(ob);
          if (c != 0) return c;
          return a.title.compareTo(b.title);
        });
    }
    final filter = uiJson['row_filter'];
    final contextBind = uiJson['context_bind'];
    final bindEntries = <MapEntry<String, String>>[];
    if (contextBind is Map && contextRowId != null) {
      for (final e in contextBind.entries) {
        if (e.value?.toString() == 'contextRowId') {
          bindEntries.add(MapEntry(e.key.toString(), contextRowId!));
        }
      }
    }
    // Legacy prompts: profile_id ← contextRowId when context_bind missing.
    if (bindEntries.isEmpty && contextRowId != null && contextBind is Map) {
      if (contextBind['profile_id'] == 'contextRowId') {
        bindEntries.add(MapEntry('profile_id', contextRowId!));
      }
    }
    final filterFromCtx = uiJson['row_filter_from_context'];
    final ctxFilterEntries = <MapEntry<String, String>>[];
    if (filterFromCtx is Map) {
      final pick = _pickContextMap();
      Map<String, dynamic> ctxBody = const {};
      if (contextRowId != null) {
        final ctx = seeds.itemById(contextRowId!);
        if (ctx is Map && ctx['body'] is Map) {
          ctxBody = Map<String, dynamic>.from(ctx['body'] as Map);
        }
      }
      for (final e in filterFromCtx.entries) {
        final ctxKey = e.value?.toString();
        if (ctxKey == null || ctxKey.isEmpty) continue;
        final v = _resolvePickOrBody(ctxKey, pick: pick, body: ctxBody);
        if (v == null || v.isEmpty) continue;
        ctxFilterEntries.add(MapEntry(e.key.toString(), v));
      }
    }
    if (filter is! Map && bindEntries.isEmpty && ctxFilterEntries.isEmpty) {
      return all;
    }
    return all.where((row) {
      final item = seeds.itemById(row.id);
      final body = item?['body'];
      if (body is! Map) return false;
      if (filter is Map) {
        for (final entry in filter.entries) {
          if (body[entry.key]?.toString() != entry.value.toString()) return false;
        }
      }
      for (final bind in bindEntries) {
        if (body[bind.key]?.toString() != bind.value) return false;
      }
      for (final bind in ctxFilterEntries) {
        if (body[bind.key]?.toString() != bind.value) return false;
      }
      return true;
    }).toList();
  }

  Map<String, String>? _pickContextMap() {
    try {
      final raw = seeds.pickContext;
      if (raw is! Map) return null;
      return {
        for (final e in raw.entries) e.key.toString(): e.value.toString(),
      };
    } catch (_) {
      return null;
    }
  }

  String? _resolvePickOrBody(
    String key, {
    required Map<String, String>? pick,
    required Map<String, dynamic>? body,
  }) {
    final fromPick = pick?[key];
    if (fromPick != null && fromPick.isNotEmpty) return fromPick;
    final fromBody = body?[key]?.toString();
    if (fromBody != null && fromBody.isNotEmpty) return fromBody;
    return null;
  }

  void _clearPickContext() {
    try {
      seeds.clearPickContext();
    } catch (_) {}
  }

  int _sortOrderOf(dynamic item) {
    if (item is! Map) return 1000;
    final body = item['body'];
    if (body is! Map) return 1000;
    final raw = body['sort_order'];
    if (raw is num) return raw.toInt();
    return int.tryParse(raw?.toString() ?? '') ?? 1000;
  }

  List<AppEntityColumn> _columns(
    Map<String, dynamic> uiJson,
    AppLocalizations l10n,
    Locale locale,
  ) {
    final raw = uiJson['columns'];
    if (raw is! List) return const [];
    return raw.whereType<Map>().map((c) {
      final labelRaw = c['label'] ?? c['field'];
      return AppEntityColumn(
        id: c['field'] as String? ?? '',
        label: resolveMetaLabel(labelRaw, l10n, locale: locale).isNotEmpty
            ? resolveMetaLabel(labelRaw, l10n, locale: locale)
            : c['field'] as String? ?? '',
        width: c['width'] is num ? (c['width'] as num).toDouble() : null,
      );
    }).where((c) => c.id.isNotEmpty).toList();
  }

  List<Widget>? _toolbar(
    BuildContext context,
    Map<String, dynamic> uiJson,
    String tableSlug,
    AppLocalizations l10n, {
    required bool skipCreate,
  }) {
    final items = <Widget>[];
    if (!skipCreate) {
      final primary = uiJson['primary_action'];
      if (!readOnly && primary is Map && primary['kind'] == 'create_row') {
        final locale = Localizations.localeOf(context);
        items.add(
          AppIconButton(
            icon: Icons.add,
            tooltip: _createLabel(uiJson, l10n, locale) ?? l10n.commonAdd,
            onPressed: () => _create(context, uiJson, tableSlug),
          ),
        );
      }
    }
    final toolbar = uiJson['toolbar'];
    if (toolbar is List) {
      for (final t in toolbar.whereType<Map>()) {
        final kind = t['kind'] as String? ?? '';
        if (kind == 'refresh') {
          items.add(
            AppIconButton(
              icon: Icons.refresh,
              tooltip: 'Refresh',
              onPressed: () => seeds.refresh(),
            ),
          );
        } else if (kind == 'invoke_action') {
          final actionId = t['action'] as String? ?? '';
          items.add(
            AppIconButton(
              icon: Icons.bolt_outlined,
              tooltip: actionId.isEmpty ? 'Action' : actionId,
              onPressed: () => _invokeAction(context, actionId, rowId: contextRowId),
            ),
          );
        }
      }
    }
    return items.isEmpty ? null : items;
  }

  String? _createLabel(
    Map<String, dynamic> uiJson,
    AppLocalizations l10n,
    Locale locale,
  ) {
    final primary = uiJson['primary_action'];
    if (primary is Map) {
      final label = primary['label'];
      if (label != null) {
        final resolved = resolveMetaLabel(label, l10n, locale: locale);
        if (resolved.isNotEmpty) return resolved;
      }
      return primary['kind'] as String?;
    }
    final empty = uiJson['empty'];
    if (empty is Map) {
      final action = empty['action'];
      if (action is Map && action['label'] != null) {
        final resolved = resolveMetaLabel(action['label'], l10n, locale: locale);
        if (resolved.isNotEmpty) return resolved;
      }
    }
    return null;
  }

  Future<void> _invokeAction(BuildContext context, String actionId, {String? rowId}) async {
    if (actionId.isEmpty) return;
    if (seeds is CabinetDataController) {
      try {
        await (seeds as CabinetDataController).invokeAction(actionId, rowId: rowId);
        if (context.mounted) {
          AppSnackBar.info(context, actionId);
        }
      } catch (e) {
        if (context.mounted) {
          AppErrors.showSnack(context, e);
        }
      }
      return;
    }
    PreviewStub.run(context, actionId);
  }

  List<AppEntityRow> _withSelection(
    BuildContext context,
    Map<String, dynamic> uiJson,
    List<AppEntityRow> rows,
  ) {
    final selection = uiJson['selection'];
    if (selection is! Map || selection['kind'] != 'single') return rows;
    final setOnContext = selection['set_on_context'];
    if (setOnContext is Map) {
      return _withContextSelection(context, uiJson, selection, setOnContext, rows);
    }
    final field = selection['field'] as String? ?? 'is_selected';
    final actionId = selection['action'] as String? ?? '';
    String? selectedId;
    for (final row in rows) {
      final item = seeds.itemById(row.id);
      final body = item?['body'];
      if (body is Map && body[field] == true) {
        selectedId = row.id;
        break;
      }
    }
    return rows.map((row) {
      return _selectionRow(
        row: row,
        selection: selection,
        selected: selectedId == row.id,
        onSelect: readOnly || actionId.isEmpty
            ? null
            : () => _invokeAction(context, actionId, rowId: row.id),
      );
    }).toList();
  }

  List<AppEntityRow> _withContextSelection(
    BuildContext context,
    Map<String, dynamic> uiJson,
    Map selection,
    Map setOnContext,
    List<AppEntityRow> rows,
  ) {
    final selectedId = _contextSelectedId(selection, setOnContext);
    return rows.map((row) {
      return _selectionRow(
        row: row,
        selection: selection,
        selected: selectedId == row.id,
        onSelect: readOnly
            ? null
            : () => _applyContextSelection(context, setOnContext, row.id),
      );
    }).toList();
  }

  String? _contextSelectedId(Map selection, Map setOnContext) {
    if (contextRowId == null) return null;
    final ctx = seeds.itemById(contextRowId!);
    final body = ctx is Map && ctx['body'] is Map
        ? Map<String, dynamic>.from(ctx['body'] as Map)
        : null;
    if (body == null) return null;
    final pick = _pickContextMap();

    final mapField = selection['match_map_field']?.toString() ??
        setOnContext['map_field']?.toString();
    final mapKeyFrom = selection['match_map_key_from_context']?.toString() ??
        setOnContext['map_key_from_context']?.toString();
    if (mapField != null &&
        mapField.isNotEmpty &&
        mapKeyFrom != null &&
        mapKeyFrom.isNotEmpty) {
      final key = _resolvePickOrBody(mapKeyFrom, pick: pick, body: body);
      if (key == null || key.isEmpty) return null;
      final map = body[mapField];
      if (map is Map) {
        final v = map[key]?.toString();
        if (v != null && v.isNotEmpty) return v;
      }
      return null;
    }

    final matchField =
        selection['match_context_field']?.toString() ?? setOnContext['field']?.toString() ?? '';
    if (matchField.isEmpty) return null;
    final v = _resolvePickOrBody(matchField, pick: pick, body: body);
    if (v != null && v.isNotEmpty) return v;
    return null;
  }

  AppEntityRow _selectionRow({
    required AppEntityRow row,
    required Map selection,
    required bool selected,
    required VoidCallback? onSelect,
  }) {
    final useSwitch = selection['control']?.toString() == 'switch';
    final trailingPlacement = selection['placement']?.toString() == 'trailing';
    Widget control;
    if (useSwitch) {
      control = Switch.adaptive(
        value: selected,
        onChanged: onSelect == null
            ? null
            : (v) {
                if (v) onSelect();
              },
      );
    } else {
      control = Radio<String>(
        value: row.id,
        groupValue: selected ? row.id : null,
        onChanged: onSelect == null ? null : (_) => onSelect(),
      );
    }

    return AppEntityRow(
      id: row.id,
      title: row.title,
      subtitle: row.subtitle,
      cells: row.cells,
      cellWidgets: row.cellWidgets,
      titleColor: row.titleColor,
      rowColor: row.rowColor,
      titleBold: row.titleBold,
      leading: trailingPlacement ? row.leading : control,
      trailing: trailingPlacement ? control : row.trailing,
    );
  }

  Future<void> _applyContextSelection(
    BuildContext context,
    Map setOnContext,
    String selectedRowId,
  ) async {
    if (contextRowId == null) return;
    final ctxItem = seeds.itemById(contextRowId!);
    if (ctxItem is! Map) return;
    final body = Map<String, dynamic>.from(
      ctxItem['body'] is Map
          ? Map<String, dynamic>.from(ctxItem['body'] as Map)
          : <String, dynamic>{},
    );
    final valueFrom = setOnContext['value_from']?.toString() ?? 'row_id';
    final selectedValue = valueFrom == 'row_id' ? selectedRowId : selectedRowId;

    final mapField = setOnContext['map_field']?.toString();
    final mapKeyFrom = setOnContext['map_key_from_context']?.toString();
    if (mapField != null &&
        mapField.isNotEmpty &&
        mapKeyFrom != null &&
        mapKeyFrom.isNotEmpty) {
      final pick = _pickContextMap();
      final key = _resolvePickOrBody(mapKeyFrom, pick: pick, body: body);
      if (key == null || key.isEmpty) return;
      final slots = Map<String, dynamic>.from(
        body[mapField] is Map
            ? Map<String, dynamic>.from(body[mapField] as Map)
            : <String, dynamic>{},
      );
      slots[key] = selectedValue;
      body[mapField] = slots;
    } else {
      final field = setOnContext['field']?.toString();
      if (field == null || field.isEmpty) return;
      body[field] = selectedValue;
    }

    final selectedItem = seeds.itemById(selectedRowId);
    final selectedBody = selectedItem is Map && selectedItem['body'] is Map
        ? Map<String, dynamic>.from(selectedItem['body'] as Map)
        : <String, dynamic>{};
    final alsoCopy = setOnContext['also_copy'];
    if (alsoCopy is List) {
      for (final entry in alsoCopy) {
        if (entry is! Map) continue;
        final from = entry['from']?.toString();
        final to = entry['to']?.toString();
        if (from == null || to == null) continue;
        body[to] = selectedBody[from];
      }
    }
    final clearFields = setOnContext['clear_fields'];
    if (clearFields is List) {
      for (final f in clearFields) {
        final key = f?.toString();
        if (key == null || key.isEmpty) continue;
        if (key == 'attrs') {
          body[key] = <String, dynamic>{};
        } else {
          body[key] = null;
        }
      }
    }
    if (setOnContext['recompute_build_totals'] == true) {
      _recomputeBuildTotals(body);
    }
    try {
      final upsert = seeds.upsertBody(contextRowId!, body);
      if (upsert is Future) await upsert;
      _clearPickContext();
      if (setOnContext['pop_after'] == true && context.mounted) {
        Navigator.of(context).maybePop();
      }
    } catch (e) {
      if (context.mounted) AppErrors.showSnack(context, e);
    }
  }

  void _recomputeBuildTotals(Map<String, dynamic> body) {
    final slotsRaw = body['slots'];
    final slots = slotsRaw is Map
        ? Map<String, dynamic>.from(slotsRaw)
        : <String, dynamic>{};
    var count = 0;
    var total = 0.0;
    for (final entry in slots.entries) {
      final itemId = entry.value?.toString();
      if (itemId == null || itemId.isEmpty) continue;
      count += 1;
      final item = seeds.itemById(itemId);
      final itemBody = item is Map && item['body'] is Map
          ? Map<String, dynamic>.from(item['body'] as Map)
          : <String, dynamic>{};
      final qtyRaw = itemBody['qty'];
      final qty = qtyRaw is num
          ? qtyRaw.toDouble()
          : double.tryParse(qtyRaw?.toString() ?? '') ?? 1.0;
      final offerId = itemBody['offer_id']?.toString();
      if (offerId == null || offerId.isEmpty) continue;
      final offer = seeds.itemById(offerId);
      final offerBody = offer is Map && offer['body'] is Map
          ? Map<String, dynamic>.from(offer['body'] as Map)
          : <String, dynamic>{};
      final priceRaw = offerBody['price'];
      final price = priceRaw is num
          ? priceRaw.toDouble()
          : double.tryParse(priceRaw?.toString() ?? '') ?? 0.0;
      total += price * (qty <= 0 ? 1.0 : qty);
    }
    body['components_count'] = count;
    body['price_total'] = total;
  }

  void _create(BuildContext context, Map<String, dynamic> uiJson, String tableSlug) {
    final rowId = seeds.createRow(tableSlug);
    final primary = uiJson['primary_action'];
    String? formView;
    if (primary is Map && primary['view'] is String) {
      formView = primary['view'] as String;
    } else {
      final rowTap = uiJson['row_tap'];
      if (rowTap is Map &&
          (rowTap['kind'] == 'open_form' || rowTap['kind'] == 'open_view')) {
        formView = rowTap['view'] as String?;
      }
    }
    if (formView != null && onOpenForm != null) {
      onOpenForm!(formView, rowId: rowId);
    }
  }
}

class _CollectionContextHeader extends StatelessWidget {
  const _CollectionContextHeader({
    required this.headerConfig,
    required this.seeds,
    required this.manifest,
    required this.contextRowId,
    required this.readOnly,
  });

  final Map<String, dynamic> headerConfig;
  final dynamic seeds;
  final ModuleMetaManifest manifest;
  final String? contextRowId;
  final bool readOnly;

  @override
  Widget build(BuildContext context) {
    final rowId = contextRowId;
    if (rowId == null || rowId.isEmpty) return const SizedBox.shrink();
    final tableSlug = headerConfig['table_slug'] as String? ?? '';
    final fieldsRaw = headerConfig['fields'];
    if (tableSlug.isEmpty || fieldsRaw is! List) {
      return const SizedBox.shrink();
    }
    final l10n = AppLocalizations.of(context);
    final locale = Localizations.localeOf(context);
    final body = Map<String, dynamic>.from(
      seeds.bodyFor(rowId) as Map? ?? const {},
    );
    final columns = manifest.columnsForTable(tableSlug);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (final raw in fieldsRaw.whereType<Map>())
          _buildField(
            context,
            Map<String, dynamic>.from(raw),
            columns: columns,
            body: body,
            rowId: rowId,
            l10n: l10n,
            locale: locale,
          ),
      ],
    );
  }

  Widget _buildField(
    BuildContext context,
    Map<String, dynamic> fieldCfg, {
    required List<Map<String, dynamic>> columns,
    required Map<String, dynamic> body,
    required String rowId,
    required AppLocalizations l10n,
    required Locale locale,
  }) {
    final columnName = fieldCfg['column'] as String? ?? '';
    if (columnName.isEmpty) return const SizedBox.shrink();
    final col = columns.cast<Map<String, dynamic>?>().firstWhere(
          (c) => c?['name'] == columnName,
          orElse: () => null,
        );
    final labelRaw = fieldCfg['label'] ?? col?['label'] ?? columnName;
    final label = resolveMetaLabel(labelRaw, l10n, locale: locale);
    final widgetKind = fieldCfg['widget'] as String? ?? 'value';
    final value = body[columnName];

    Future<void> persist(dynamic next) async {
      try {
        final patch = seeds.patchField(rowId, columnName, next);
        if (patch is Future) await patch;
      } catch (e) {
        if (context.mounted) AppErrors.showSnack(context, e);
      }
    }

    if (widgetKind == 'project_multiselect') {
      return ProjectMultiselectField(
        label: label,
        value: value,
        readOnly: readOnly,
        subtitleMode: fieldCfg['subtitle']?.toString(),
        onChanged: (ids) => persist(ids),
      );
    }

    return AppValuePreference<String>(
      title: label.isNotEmpty ? label : columnName,
      value: value?.toString() ?? '',
      icon: Icons.badge_outlined,
      enabled: !readOnly,
      onSave: (v) async => persist(v.trim()),
    );
  }
}

class _CollectionInlineAddHost extends StatelessWidget {
  const _CollectionInlineAddHost({
    required this.inlineConfig,
    required this.tableSlug,
    required this.uiJson,
    required this.seeds,
    this.contextRowId,
  });

  final Map<String, dynamic> inlineConfig;
  final String tableSlug;
  final Map<String, dynamic> uiJson;
  final dynamic seeds;
  final String? contextRowId;

  Future<void> _save(String raw) async {
    final field = inlineConfig['field'] as String? ?? 'name';
    final body = Map<String, dynamic>.from(
      Map<dynamic, dynamic>.from(seeds.defaultBodyForTable(tableSlug) as Map),
    );
    body[field] = raw.trim();
    final filter = uiJson['row_filter'];
    if (filter is Map) {
      body.addAll(Map<String, dynamic>.from(filter));
    }
    final contextBind = uiJson['context_bind'];
    if (contextRowId != null && contextBind is Map) {
      for (final e in contextBind.entries) {
        if (e.value?.toString() == 'contextRowId') {
          body[e.key.toString()] = contextRowId;
        }
      }
    } else if (contextRowId != null) {
      // Legacy prompts binding.
      body['profile_id'] = contextRowId;
    }
    // Prefer typed async create with initial body. Never use runtimeType strings
    // (dart2js minifies class names) + sync createRow fake pending ids.
    final adapter = seeds;
    if (adapter is RuntimeDataAdapter) {
      await adapter.createRowAsync(tableSlug, initial: body);
      return;
    }
    if (adapter is CabinetDataController) {
      await adapter.createRow(tableSlug, initial: body);
      return;
    }
    if (adapter is OwnerModuleDataController) {
      await adapter.createRow(tableSlug, initial: body);
      return;
    }
    final rowId = adapter.createRow(tableSlug) as String;
    final upsert = adapter.upsertBody(rowId, body);
    if (upsert is Future) await upsert;
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final locale = Localizations.localeOf(context);
    final titleRaw = inlineConfig['title'] ?? inlineConfig['label'];
    final title = resolveMetaLabel(titleRaw, l10n, locale: locale);
    final hintRaw = inlineConfig['hintText'] ?? titleRaw;
    final hint = resolveMetaLabel(hintRaw, l10n, locale: locale);

    return AppInlineAddField(
      title: title.isNotEmpty ? title : l10n.commonAdd,
      hintText: hint.isNotEmpty ? hint : null,
      validator: (raw) => raw.trim().isNotEmpty,
      onSave: _save,
    );
  }
}
