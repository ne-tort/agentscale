import 'dart:math' as math;
import 'dart:async';

import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/app_value_preference.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_icon_button.dart';
import 'package:prodavan/core/widgets/app_inline_add_field.dart';
import 'package:prodavan/core/widgets/app_snack_bar.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/meta/module_cell_format.dart';
import 'package:prodavan/features/meta/meta_icon.dart';
import 'package:prodavan/features/meta/meta_label.dart';
import 'package:prodavan/features/meta/module_action_file_download.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/features/meta/poll_while.dart';
import 'package:prodavan/features/meta/preview/preview_stub.dart';
import 'package:prodavan/features/meta/runtime/cabinet_data_controller.dart';
import 'package:prodavan/features/meta/runtime/module_runtime_scope.dart';
import 'package:prodavan/features/meta/runtime/owner_module_data_controller.dart';
import 'package:prodavan/features/meta/runtime/runtime_data_adapter.dart';
import 'package:prodavan/features/meta/module_scaffold_actions.dart';
import 'package:prodavan/features/meta/widgets/equipment_match_page.dart';
import 'package:prodavan/features/meta/widgets/benefit_badge.dart';
import 'package:prodavan/features/meta/widgets/budget_summary_strip.dart';
import 'package:prodavan/features/meta/widgets/document_fields_panel.dart';
import 'package:prodavan/features/meta/widgets/editable_number_cell.dart';
import 'package:prodavan/features/meta/widgets/file_upload_field.dart';
import 'package:prodavan/features/meta/widgets/project_multiselect_field.dart';
import 'package:prodavan/l10n/app_localizations.dart';

double? _doubleFromUi(Object? raw) {
  if (raw is num) return raw.toDouble();
  if (raw is String) return double.tryParse(raw.trim());
  return null;
}

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
    final uiJson0 = Map<String, dynamic>.from(ui);
    // Кросс-чатовая вьюха (Закупка → товары поставщика): подгружаем строки
    // всех чатов проекта один раз; дальше они обновляются через loadAll.
    final dataScope = uiJson0['data_scope'];
    if (dataScope is Map && dataScope['chats'] == 'all') {
      final s = seeds;
      if (s is RuntimeDataAdapter) {
        unawaited(s.ensureCrossChat(tableSlug));
      }
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
        // Primary column layout follows the title_field column entry config.
        final primaryEntry = allColumns.firstWhere(
          (c) => c.id == titleField,
          orElse: () => AppEntityColumn(id: titleField, label: primaryLabel),
        );
        final serverPaged = uiJson['server_paged'] == true && seeds is RuntimeDataAdapter;
        final rawRows = _filteredRows(seeds, tableSlug, uiJson);
        var styled = _applyRowStyles(context, uiJson, rawRows);
        styled = _withNoOfferPlaceholder(context, uiJson, styled);
        styled = _withDashEmptyCells(context, uiJson, styled);
        styled = _withWarningCells(context, uiJson, styled);
        var rows = _withSelection(context, uiJson, styled);
        rows = _withEditableCells(context, uiJson, tableSlug, rows);
        rows = _withBenefitBadges(context, uiJson, l10n, rows);
        final hasInline = _hasInlineAdd(uiJson);
        final hasPoll = _hasActivePoll(uiJson, rawRows, seeds);
        final onLoadAction = _onLoadAction(uiJson);
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
          primaryMaxLines: primaryEntry.maxLines,
          primaryMaxWidth: primaryEntry.maxWidth,
          primaryWidth: primaryEntry.width,
          rowMinHeight: _doubleFromUi(uiJson['row_min_height']),
          externalScroll: true,
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
            if (rowTap is Map && rowTap['kind'] == 'match_product') {
              // виртуальные строки (поиск/мастер-прайс): отдельная страница
              // сопоставления товара с позицией заказчика
              final actionId = rowTap['action']?.toString() ?? '';
              final adapter = seeds;
              if (actionId.isNotEmpty && adapter is RuntimeDataAdapter) {
                Navigator.of(context).push(
                  MaterialPageRoute<void>(
                    builder: (_) => EquipmentMatchPage(
                      adapter: adapter,
                      actionId: actionId,
                      srcHash: row.id,
                      productTitle: row.title,
                    ),
                  ),
                );
              }
              return;
            }
            if (rowTap is Map && rowTap['kind'] == 'invoke_action') {
              // тап по строке вызывает экшен (напр. выбор оффера в «Закупке»)
              final actionId = rowTap['action']?.toString() ?? '';
              if (actionId.isNotEmpty) {
                unawaited(_invokeAction(context, actionId, rowId: row.id));
              }
              return;
            }
            if (rowTap is Map && onOpenForm != null) {
              final kind = rowTap['kind'] as String?;
              final targetView = rowTap['view'] as String?;
              if (targetView != null &&
                  (kind == 'open_form' || kind == 'open_view')) {
                onOpenForm!(targetView, rowId: _rowTapRowId(rowTap, row.id));
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

        // A summary strip (e.g. budget_totals) must also force the Column
        // layout - otherwise the early return below hides it for views
        // without inline add / headers / poll (the budget view).
        final summary = _summary(uiJson);
        final docFields = _docFieldsConfig(uiJson);
        final serverBar = serverPaged
            ? _serverPagedBar(
                context,
                seeds as RuntimeDataAdapter,
                tableSlug,
                stockFilter: uiJson['stock_filter'] == true,
              )
            : null;
        if (!hasInline &&
            !_hasContextHeader(uiJson) &&
            !_hasListHeader(uiJson) &&
            !hasPoll &&
            onLoadAction == null &&
            summary == null &&
            docFields == null) {
          if (serverBar == null) return SingleChildScrollView(child: collection);
          return SingleChildScrollView(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              mainAxisSize: MainAxisSize.min,
              children: [serverBar, collection],
            ),
          );
        }

        final docFieldsPanel = docFields == null
            ? null
            : _buildDocFieldsPanel(docFields, context);

        return SingleChildScrollView(
          child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          mainAxisSize: MainAxisSize.min,
          children: [
            if (onLoadAction != null)
              _CollectionOnLoad(
                seeds: seeds,
                actionId: onLoadAction,
              ),
            if (hasPoll)
              _CollectionPoller(
                seeds: seeds,
                uiJson: uiJson,
                rows: rawRows,
              ),
            if (summary != null || docFieldsPanel != null)
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  // три равных блока в линию: сводка | Поставщик | Сделка
                  // (панель реквизитов сама делит свои 2/3 на две карточки)
                  if (summary != null) Expanded(child: summary),
                  if (docFieldsPanel != null)
                    Expanded(
                      flex: 2,
                      child: Padding(
                        // у сводки свой lr-md/xs-top паддинг — выравниваем панель
                        padding: const EdgeInsets.fromLTRB(0, AppSpacing.xs, AppSpacing.md, 0),
                        child: docFieldsPanel,
                      ),
                    ),
                ],
              ),
            if (_hasListHeader(uiJson))
              _CollectionListHeader(
                headerConfig: Map<String, dynamic>.from(uiJson['list_header'] as Map),
                seeds: seeds,
                manifest: manifest,
                readOnly: readOnly,
              ),
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
            if (serverBar != null) serverBar,
            collection,
          ],
          ),
        );
      },
    );
  }

  /// Поиск + пагинатор серверной пагинации (виртуальные таблицы из OS).
  Widget _serverPagedBar(
    BuildContext context,
    RuntimeDataAdapter adapter,
    String tableSlug, {
    bool stockFilter = false,
  }) {
    // stockFilter участвует только в шапке страницы (см. buildModuleScaffoldActions).
    final scheme = Theme.of(context).colorScheme;
    final page = adapter.serverPage(tableSlug);
    final pageSize = adapter.serverPageSize(tableSlug);
    final total = adapter.serverTotal(tableSlug);
    final from = total == 0 ? 0 : (page - 1) * pageSize + 1;
    final to = math.min(page * pageSize, total);
    final lastPage = total == 0 ? 1 : ((total + pageSize - 1) ~/ pageSize);
    final currentSearch = adapter.serverSearch(tableSlug);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        // Inline-поле как у добавления итемов на других страницах, но поиск.
        AppInlineAddField(
          title: 'Найти товар...',
          hintText: 'Найти товар...',
          showBottomDivider: false,
          icon: Icons.search,
          actionTooltip: 'Найти',
          validator: (raw) => raw.trim().isNotEmpty,
          onSave: (raw) => adapter.setServerSearch(tableSlug, raw.trim()),
        ),
        if (currentSearch.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(bottom: AppSpacing.xs),
            child: Align(
              alignment: Alignment.centerLeft,
              child: InputChip(
                label: Text(currentSearch, overflow: TextOverflow.ellipsis),
                onDeleted: () => unawaited(adapter.setServerSearch(tableSlug, '')),
              ),
            ),
          ),
        Padding(
      padding: const EdgeInsets.fromLTRB(0, 0, 0, AppSpacing.xs),
      child: Row(
        children: [
          const Spacer(),
          Text(
            '$from–$to из $total',
            style: TextStyle(color: scheme.onSurfaceVariant, fontSize: 12),
          ),
          IconButton(
            visualDensity: VisualDensity.compact,
            tooltip: 'Предыдущая страница',
            onPressed: page > 1
                ? () => unawaited(adapter.setServerPage(tableSlug, page - 1))
                : null,
            icon: const Icon(Icons.chevron_left),
          ),
          Text('$page / $lastPage',
              style: TextStyle(color: scheme.onSurfaceVariant, fontSize: 12)),
          IconButton(
            visualDensity: VisualDensity.compact,
            tooltip: 'Следующая страница',
            onPressed: page < lastPage
                ? () => unawaited(adapter.setServerPage(tableSlug, page + 1))
                : null,
            icon: const Icon(Icons.chevron_right),
          ),
        ],
      ),
        ),
      ],
    );
  }

  bool _hasListHeader(Map<String, dynamic> uiJson) {
    final header = uiJson['list_header'];
    return header is Map && (header['table_slug'] as String? ?? '').isNotEmpty;
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
        if (kind == 'success') {
          accent = colors.success;
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
        } else if (e.value is Map && e.value['field'] is String) {
          // WAVE7: привязка поля строки к полю контекстного ряда
          // (например supplier_offers.seller ← procurement.seller).
          final ctx = seeds.itemById(contextRowId!);
          final ctxBody = ctx is Map ? ctx['body'] : null;
          final v = ctxBody is Map
              ? ctxBody[e.value['field']]?.toString()
              : null;
          if (v != null && v.isNotEmpty) {
            bindEntries.add(MapEntry(e.key.toString(), v));
          }
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
        // `contextRowId` — сам открытый ряд (родитель pick-вида): слот сборки
        // фильтрует кандидатов по build_id = row_id сборки.
        if (ctxKey == 'contextRowId') {
          if (contextRowId != null && contextRowId!.isNotEmpty) {
            ctxFilterEntries.add(MapEntry(e.key.toString(), contextRowId!));
          }
          continue;
        }
        final v = _resolvePickOrBody(ctxKey, pick: pick, body: ctxBody);
        if (v == null || v.isEmpty) continue;
        ctxFilterEntries.add(MapEntry(e.key.toString(), v));
      }
    }
    if (filter is! Map && bindEntries.isEmpty && ctxFilterEntries.isEmpty) {
      return _applySort(uiJson, all);
    }
    return _applySort(
      uiJson,
      all.where((row) {
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
      }).toList(),
    );
  }

  /// `ui_json.sort: [{field, dir}]` — стабильная сортировка рядов по полям body
  /// (num сравнивается численно, остальное — строками; null/пусто — в конце).
  List<AppEntityRow> _applySort(
    Map<String, dynamic> uiJson,
    List<AppEntityRow> rows,
  ) {
    final raw = uiJson['sort'];
    if (raw is! List || raw.isEmpty) return rows;
    final specs = <MapEntry<String, int>>[];
    for (final s in raw.whereType<Map>()) {
      final field = s['field']?.toString() ?? '';
      if (field.isEmpty) continue;
      specs.add(MapEntry(field, s['dir']?.toString() == 'desc' ? -1 : 1));
    }
    if (specs.isEmpty) return rows;
    final list = [...rows];
    list.sort((a, b) {
      for (final spec in specs) {
        final av = _sortValueOf(a.id, spec.key);
        final bv = _sortValueOf(b.id, spec.key);
        int cmp;
        if (av == null && bv == null) {
          cmp = 0;
        } else if (av == null) {
          cmp = 1;
        } else if (bv == null) {
          cmp = -1;
        } else if (av is num && bv is num) {
          cmp = av.compareTo(bv);
        } else {
          cmp = av.toString().toLowerCase().compareTo(bv.toString().toLowerCase());
        }
        if (cmp != 0) return cmp * spec.value;
      }
      return 0;
    });
    return list;
  }

  dynamic _sortValueOf(String rowId, String field) {
    final item = seeds.itemById(rowId);
    final body = item is Map ? item['body'] : null;
    if (body is! Map) return null;
    final raw = body[field];
    if (raw == null) return null;
    if (raw.toString().isEmpty) return null;
    return raw;
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
      final alignRaw = c['align']?.toString() ?? '';
      final formatRaw = c['format']?.toString() ?? '';
      return AppEntityColumn(
        id: c['field'] as String? ?? '',
        label: resolveMetaLabel(labelRaw, l10n, locale: locale).isNotEmpty
            ? resolveMetaLabel(labelRaw, l10n, locale: locale)
            : c['field'] as String? ?? '',
        width: c['width'] is num ? (c['width'] as num).toDouble() : null,
        maxWidth: c['max_width'] is num ? (c['max_width'] as num).toDouble() : null,
        maxLines: c['max_lines'] is num ? (c['max_lines'] as num).toInt().clamp(1, 8) : 1,
        selectionOnly: c['selection_only'] == true,
        align: switch (alignRaw) {
          'center' => AppEntityColumnAlign.center,
          'end' => AppEntityColumnAlign.end,
          _ => formatRaw == 'budget_calc' || formatRaw == 'money'
              ? AppEntityColumnAlign.end
              : AppEntityColumnAlign.start,
        },
      );
    }).where((c) => c.id.isNotEmpty).toList();
  }

  /// `editable: true` columns render tap-to-edit cells (numeric).
  List<String> _editableFields(Map<String, dynamic> uiJson) {
    final raw = uiJson['columns'];
    if (raw is! List || readOnly) return const [];
    return raw
        .whereType<Map>()
        .where((c) => c['editable'] == true && (c['field'] as String?) != null)
        .map((c) => c['field'] as String)
        .where((f) => f.isNotEmpty)
        .toList();
  }

  /// `editable: true` columns render tap-to-edit cells (numeric) or tap-to-toggle
  /// cells (bool — например include_delivery в «Закупке»).
  /// `format: "benefit"` columns: solid badges comparing each row's price to
  /// the current (selected/best) offer of the same position.
  List<AppEntityRow> _withBenefitBadges(
    BuildContext context,
    Map<String, dynamic> uiJson,
    AppLocalizations l10n,
    List<AppEntityRow> rows,
  ) {
    final raw = uiJson['columns'];
    if (raw is! List || rows.isEmpty) return rows;
    final configs = <Map<String, dynamic>>[];
    for (final c in raw) {
      if (c is Map && c['format']?.toString() == 'benefit') {
        configs.add(Map<String, dynamic>.from(c));
      }
    }
    // benefit_static: серверный бейдж (пайплайн пишет benefit_label/tone) —
    // используется там, где конкуренты позиции не видны в списке (Закупка).
    final staticConfigs = <Map<String, dynamic>>[];
    for (final c in raw) {
      if (c is Map && c['format']?.toString() == 'benefit_static') {
        staticConfigs.add(Map<String, dynamic>.from(c));
      }
    }
    if (staticConfigs.isNotEmpty) {
      rows = _withStaticBenefitBadges(rows, staticConfigs);
    }
    if (configs.isEmpty) return rows;

    Map<String, dynamic> bodyOf(String rowId) {
      final item = seeds.itemById(rowId);
      return item is Map && item['body'] is Map
          ? Map<String, dynamic>.from(item['body'] as Map)
          : const <String, dynamic>{};
    }

    final out = List<AppEntityRow>.of(rows);
    for (final cfg in configs) {
      final columnId = cfg['field']?.toString();
      if (columnId == null || columnId.isEmpty) continue;
      final priceField = cfg['price_field']?.toString() ?? 'price';
      final currentField = cfg['current_field']?.toString() ?? 'is_selected';
      final groupField = cfg['group_field']?.toString();

      double? priceOf(String rowId) {
        final v = bodyOf(rowId)[priceField];
        if (v is num) return v.toDouble();
        if (v is String) return double.tryParse(v.trim());
        return null;
      }

      final badges = computeBenefitBadges(
        rowIds: rows.map((r) => r.id),
        priceOf: priceOf,
        currentOf: (rowId) => bodyOf(rowId)[currentField] == true,
        groupOf: (rowId) =>
            groupField == null ? '' : bodyOf(rowId)[groupField]?.toString() ?? '',
        l10n: l10n,
      );
      if (badges.isEmpty) continue;
      for (var i = 0; i < out.length; i++) {
        final row = out[i];
        final badge = badges[row.id];
        if (badge == null) continue;
        final widgets = Map<String, Widget>.from(row.cellWidgets);
        widgets[columnId] = BenefitBadge(data: badge);
        out[i] = AppEntityRow(
          id: row.id,
          title: row.title,
          subtitle: row.subtitle,
          cells: row.cells,
          cellWidgets: widgets,
          leading: row.leading,
          trailing: row.trailing,
          titleColor: row.titleColor,
          rowColor: row.rowColor,
          titleBold: row.titleBold,
        );
      }
    }
    return out;
  }

  /// Группа без офферов: в колонке «Товар» (primary) — «Нет оффера»
  /// warning-цветом вместо подстановки партномера.
  /// ui_json: `no_offers_placeholder: {"text": "Нет оффера"}`.
  List<AppEntityRow> _withNoOfferPlaceholder(
    BuildContext context,
    Map<String, dynamic> uiJson,
    List<AppEntityRow> rows,
  ) {
    final cfg = uiJson['no_offers_placeholder'];
    if (cfg is! Map) return rows;
    final text = cfg['text']?.toString() ?? 'Нет оффера';
    final warning = context.appColors.warning;
    return rows.map((row) {
      final item = seeds.itemById(row.id);
      final body = item is Map && item['body'] is Map
          ? Map<String, dynamic>.from(item['body'] as Map)
          : const <String, dynamic>{};
      final offers = body['offers_count'];
      final noOffers = offers is num ? offers <= 0 : (offers == null);
      if (!noOffers) return row;
      return AppEntityRow(
        id: row.id,
        title: text,
        subtitle: row.subtitle,
        cells: row.cells,
        cellWidgets: row.cellWidgets,
        leading: row.leading,
        trailing: row.trailing,
        titleColor: warning,
        rowColor: row.rowColor,
        titleBold: row.titleBold,
      );
    }).toList();
  }

  /// Колонки из `dash_empty_fields`: пустое значение → «—» (muted).
  List<AppEntityRow> _withDashEmptyCells(
    BuildContext context,
    Map<String, dynamic> uiJson,
    List<AppEntityRow> rows,
  ) {
    final raw = uiJson['dash_empty_fields'];
    if (raw is! List || raw.isEmpty) return rows;
    final fields = raw.map((e) => e.toString()).toSet();
    final muted = context.appColors.muted;
    return rows.map((row) {
      final widgets = Map<String, Widget>.from(row.cellWidgets);
      var changed = false;
      for (final field in fields) {
        if (widgets.containsKey(field)) continue;
        final value = row.cells[field]?.toString() ?? '';
        if (value.trim().isEmpty) {
          widgets[field] = Text('—', style: TextStyle(color: muted));
          changed = true;
        }
      }
      if (!changed) return row;
      return AppEntityRow(
        id: row.id,
        title: row.title,
        subtitle: row.subtitle,
        cells: row.cells,
        cellWidgets: widgets,
        leading: row.leading,
        trailing: row.trailing,
        titleColor: row.titleColor,
        rowColor: row.rowColor,
        titleBold: row.titleBold,
      );
    }).toList();
  }

  /// Warning-ячейки трёх видов (конфиг колонок ui_json):
  /// - `format: budget_price_in` — «Нет цены» / «Под заказ» /
  ///   «{цена} (Под заказ)» вместо числа (budgetPriceInLabel);
  /// - `format: offer_price` — «Нет цены» warning, когда цены нет
  ///   (включая текстовые «Уточняйте»: price_num не парсится);
  /// - `warning_when_match: [...]` — текст ячейки красится warning,
  ///   когда body['match_kind'] в списке (аналоги/сомнения в бюджете).
  List<AppEntityRow> _withWarningCells(
    BuildContext context,
    Map<String, dynamic> uiJson,
    List<AppEntityRow> rows,
  ) {
    final columns = uiJson['columns'];
    if (columns is! List) return rows;
    final priceInFields = <String>[];
    final offerPriceFields = <String>[];
    final stockFields = <String>[];
    final matchWarn = <String, Set<String>>{};
    for (final c in columns.whereType<Map>()) {
      final field = c['field']?.toString() ?? '';
      if (field.isEmpty) continue;
      switch (c['format']?.toString()) {
        case 'budget_price_in':
          priceInFields.add(field);
        case 'offer_price':
          offerPriceFields.add(field);
        case 'stock_label':
          stockFields.add(field);
      }
      final when = c['warning_when_match'];
      if (when is List && when.isNotEmpty) {
        matchWarn[field] = when.map((e) => e.toString()).toSet();
      }
    }
    if (priceInFields.isEmpty &&
        offerPriceFields.isEmpty &&
        stockFields.isEmpty &&
        matchWarn.isEmpty) {
      return rows;
    }
    final warning = context.appColors.warning;
    return rows.map((row) {
      final item = seeds.itemById(row.id);
      final body = item is Map && item['body'] is Map
          ? Map<String, dynamic>.from(item['body'] as Map)
          : const <String, dynamic>{};
      final widgets = Map<String, Widget>.from(row.cellWidgets);
      var changed = false;
      if (priceInFields.isNotEmpty) {
        final label = budgetPriceInLabel(body);
        if (label != null) {
          for (final field in priceInFields) {
            widgets[field] = Text(label, style: TextStyle(color: warning));
          }
          changed = true;
        }
      }
      if (offerPriceFields.isNotEmpty && body[offerPriceFields.first] == null) {
        // все offer_price-колонки строки делят одно состояние «нет цены»
        for (final field in offerPriceFields) {
          if (body[field] == null) {
            widgets[field] = Text('Нет цены', style: TextStyle(color: warning));
            changed = true;
          }
        }
      }
      if (stockFields.isNotEmpty) {
        for (final field in stockFields) {
          if (body[field] == true) continue; // в наличии — обычный текст
          widgets[field] = Text('Под заказ', style: TextStyle(color: warning));
          changed = true;
        }
      }
      if (matchWarn.isNotEmpty) {
        final kind = body['match_kind']?.toString() ?? '';
        for (final entry in matchWarn.entries) {
          if (!entry.value.contains(kind)) continue;
          final text = row.cells[entry.key] ?? '';
          widgets[entry.key] = Text(text, style: TextStyle(color: warning));
          changed = true;
        }
      }
      if (!changed) return row;
      return AppEntityRow(
        id: row.id,
        title: row.title,
        subtitle: row.subtitle,
        cells: row.cells,
        cellWidgets: widgets,
        leading: row.leading,
        trailing: row.trailing,
        titleColor: row.titleColor,
        rowColor: row.rowColor,
        titleBold: row.titleBold,
      );
    }).toList();
  }

  /// benefit_static: бейдж из серверных полей benefit_label/benefit_tone
  /// (их пишет equipment pipeline; виджет-окраска как у клиентского benefit).
  List<AppEntityRow> _withStaticBenefitBadges(
    List<AppEntityRow> rows,
    List<Map<String, dynamic>> configs,
  ) {
    BenefitTone toneOf(String raw) => switch (raw) {
          'best' => BenefitTone.best,
          'better' => BenefitTone.better,
          'worse' => BenefitTone.worse,
          _ => BenefitTone.same,
        };
    return rows.map((row) {
      final item = seeds.itemById(row.id);
      final body = item is Map && item['body'] is Map
          ? Map<String, dynamic>.from(item['body'] as Map)
          : const <String, dynamic>{};
      var widgets = Map<String, Widget>.from(row.cellWidgets);
      var changed = false;
      for (final cfg in configs) {
        final columnId = cfg['field']?.toString() ?? '';
        if (columnId.isEmpty) continue;
        final labelField = cfg['label_field']?.toString() ?? 'benefit_label';
        final toneField = cfg['tone_field']?.toString() ?? 'benefit_tone';
        final label = body[labelField]?.toString() ?? '';
        if (label.isEmpty) continue;
        widgets[columnId] = BenefitBadge(
          data: BenefitBadgeData(
            label: label,
            tone: toneOf(body[toneField]?.toString() ?? ''),
          ),
        );
        changed = true;
      }
      if (!changed) return row;
      return AppEntityRow(
        id: row.id,
        title: row.title,
        subtitle: row.subtitle,
        cells: row.cells,
        cellWidgets: widgets,
        leading: row.leading,
        trailing: row.trailing,
        titleColor: row.titleColor,
        rowColor: row.rowColor,
        titleBold: row.titleBold,
      );
    }).toList();
  }

  List<AppEntityRow> _withEditableCells(
    BuildContext context,
    Map<String, dynamic> uiJson,
    String tableSlug,
    List<AppEntityRow> rows,
  ) {
    final fields = _editableFields(uiJson);
    if (fields.isEmpty) return rows;
    return rows.map((row) {
      final item = seeds.itemById(row.id);
      final body = item is Map && item['body'] is Map
          ? Map<String, dynamic>.from(item['body'] as Map)
          : const <String, dynamic>{};
      final widgets = Map<String, Widget>.from(row.cellWidgets);
      for (final field in fields) {
        if (widgets.containsKey(field)) continue;
        if (body[field] is bool) {
          widgets[field] = _EditableBoolCell(
            value: body[field] as bool,
            onToggle: () async {
              try {
                final patch = seeds.patchField(row.id, field, !(body[field] as bool));
                if (patch is Future) await patch;
              } catch (e) {
                if (context.mounted) AppErrors.showSnack(context, e);
                rethrow;
              }
            },
          );
          continue;
        }
        widgets[field] = EditableNumberCell(
          value: body[field],
          align: TextAlign.end,
          onSubmit: (parsed) async {
            try {
              final patch = seeds.patchField(row.id, field, parsed);
              if (patch is Future) await patch;
            } catch (e) {
              if (context.mounted) AppErrors.showSnack(context, e);
              rethrow;
            }
          },
        );
      }
      return AppEntityRow(
        id: row.id,
        title: row.title,
        subtitle: row.subtitle,
        cells: row.cells,
        cellWidgets: widgets,
        leading: row.leading,
        trailing: row.trailing,
        titleColor: row.titleColor,
        rowColor: row.rowColor,
        titleBold: row.titleBold,
      );
    }).toList();
  }

  Widget? _summary(Map<String, dynamic> uiJson) {
    final raw = uiJson['summary'];
    if (raw is! Map) return null;
    final kind = raw['kind']?.toString();
    if (kind != 'budget_totals') return null;
    final items = _allItems(seeds, _tableSlugOf(uiJson));
    final bodies = items
        .map((item) => item['body'])
        .whereType<Map>()
        .map((b) => Map<String, dynamic>.from(b))
        .toList();
    return BudgetSummaryStrip(bodies: bodies);
  }

  String _tableSlugOf(Map<String, dynamic> uiJson) {
    return view['table_slug'] as String? ?? '';
  }

  /// `ui_json.doc_fields` — панель реквизитов документов (Бюджетирование):
  /// компания (весь кабинет) + сделка (чат).
  Map<String, dynamic>? _docFieldsConfig(Map<String, dynamic> uiJson) {
    final raw = uiJson['doc_fields'];
    if (raw is! Map) return null;
    final companyTable = raw['company_table']?.toString() ?? '';
    final dealTable = raw['deal_table']?.toString() ?? '';
    if (companyTable.isEmpty || dealTable.isEmpty) return null;
    final companyFields = (raw['company_fields'] as List? ?? const [])
        .whereType<Map>()
        .map((f) => f['column']?.toString() ?? '')
        .where((f) => f.isNotEmpty)
        .toList();
    final dealFields = (raw['deal_fields'] as List? ?? const [])
        .whereType<Map>()
        .where((f) => (f['column']?.toString() ?? '').isNotEmpty)
        .map((f) => Map<String, dynamic>.from(f))
        .toList();
    if (companyFields.isEmpty && dealFields.isEmpty) return null;
    return {
      'company_table': companyTable,
      'deal_table': dealTable,
      'company_title': raw['company_title'],
      'deal_title': raw['deal_title'],
      'company_fields': companyFields,
      'deal_fields': dealFields,
    };
  }

  Widget? _buildDocFieldsPanel(
    Map<String, dynamic> config,
    BuildContext context,
  ) {
    if (readOnly) return null;
    final companyTable = config['company_table'] as String;
    final dealTable = config['deal_table'] as String;
    final locale = Localizations.localeOf(context);
    final l10n = AppLocalizations.of(context);
    // подписи и дефолты (из шаблона) — из меты колонок обеих таблиц
    final labels = <String, String>{};
    final defaults = <String, Object?>{};
    for (final tableSlug in {companyTable, dealTable}) {
      for (final col in manifest.columnsForTable(tableSlug)) {
        final name = col['name']?.toString() ?? '';
        if (name.isEmpty) continue;
        labels[name] = resolveMetaLabel(col['label'], l10n, locale: locale);
        if (col.containsKey('default')) defaults[name] = col['default'];
      }
    }
    return DocumentFieldsPanel(
      companyTable: companyTable,
      dealTable: dealTable,
      companyTitle: config['company_title'],
      dealTitle: config['deal_title'],
      companyFields: (config['company_fields'] as List).cast<String>(),
      dealFields: (config['deal_fields'] as List).cast<Map<String, dynamic>>(),
      labels: labels,
      defaults: defaults,
      itemsForTable: (slug) => _allItems(seeds, slug)
          .map((m) => Map<String, dynamic>.from(m))
          .toList(),
      createRow: (slug) async {
        final created = seeds.createRow(slug);
        return created is Future ? await created as String : created as String;
      },
      upsertBody: (rowId, body) async {
        final upd = seeds.upsertBody(rowId, body);
        if (upd is Future) await upd;
      },
    );
  }

  List<Map> _allItems(dynamic seeds, String tableSlug) {
    try {
      final rows = seeds.itemsForTable(tableSlug);
      return rows.whereType<Map>().toList();
    } catch (_) {
      return const [];
    }
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
              onPressed: () async {
                try {
                  await seeds.loadAll();
                } catch (_) {
                  seeds.refresh();
                }
              },
            ),
          );
        } else if (kind == 'invoke_action') {
          final actionId = t['action'] as String? ?? '';
          final actionLabel = resolveMetaLabel(
            t['label'],
            l10n,
            locale: Localizations.localeOf(context),
          );
          items.add(
            AppIconButton(
              icon: metaIconFromName(
                t['icon'] as String?,
                fallback: Icons.bolt_outlined,
              ),
              tooltip: actionLabel.isNotEmpty
                  ? actionLabel
                  : (actionId.isEmpty ? 'Action' : actionId),
              onPressed: () => _invokeAction(context, actionId, rowId: contextRowId),
            ),
          );
        } else if ((kind == 'open_view' || kind == 'open_form') && onOpenForm != null) {
          final targetView = t['view'] as String? ?? '';
          if (targetView.isEmpty) continue;
          final label = resolveMetaLabel(t['label'], l10n, locale: Localizations.localeOf(context));
          final iconName = t['icon'] as String?;
          final passContext = t['context'] == 'row' || t['context_row'] == true;
          items.add(
            AppIconButton(
              icon: metaIconFromName(iconName),
              tooltip: label.isNotEmpty ? label : targetView,
              onPressed: () => onOpenForm!(
                targetView,
                rowId: passContext ? contextRowId : null,
              ),
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
    final controller = switch (seeds) {
      CabinetDataController c => c,
      RuntimeDataAdapter a => a.controller,
      _ => null,
    };
    if (controller != null) {
      try {
        final result = await controller.invokeAction(actionId, rowId: rowId);
        final savedFile = await _saveActionFileRef(controller, result);
        if (context.mounted) {
          if (savedFile) {
            AppSnackBar.success(
              context,
              AppLocalizations.of(context).projectWorkspaceDownloaded,
            );
          } else {
            AppSnackBar.success(
              context,
              moduleActionLabel(
                context,
                actionId,
                manifest.actions,
              ),
            );
          }
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

  /// row_tap `context_field`: open the linked row (its id lives in the tapped
  /// row's body) instead of the tapped row itself. Falls back to [rowId] when
  /// the field is unset or empty.
  String? _rowTapRowId(Map rowTap, String rowId) {
    final contextField = rowTap['context_field']?.toString();
    if (contextField == null || contextField.isEmpty) return rowId;
    final item = seeds.itemById(rowId);
    final body = item is Map ? item['body'] : null;
    final linked = body is Map ? body[contextField]?.toString() : null;
    if (linked != null && linked.isNotEmpty) return linked;
    return rowId;
  }

  /// Downloads the `file_ref` returned by a module action (e.g. budget
  /// export) and saves it through the desktop save dialog. True when a file
  /// was saved, false when there is nothing to download.
  Future<bool> _saveActionFileRef(
    CabinetDataController controller,
    Map<String, dynamic> result,
  ) async {
    return saveModuleActionFile(controller, result);
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
    final controlKind = selection['control']?.toString();
    final useSwitch = controlKind == 'switch';
    // WAVE7: чекбокс ведёт себя как single (сервер снимает выбор у других
    // рядов группы), но выглядит как множественный выбор («Закупка»).
    final useCheckbox = controlKind == 'checkbox';
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
    } else if (useCheckbox) {
      control = Checkbox(
        value: selected,
        onChanged: onSelect == null ? null : (_) => onSelect(),
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
    // WAVE11: количество на слот ({type_id: qty}) — свойство слота, а не
    // кандидата; явный slot_qty важнее qty легаси-комплектующего.
    final slotQtyRaw = body['slot_qty'];
    final slotQty = slotQtyRaw is Map
        ? Map<String, dynamic>.from(slotQtyRaw)
        : <String, dynamic>{};

    double qtyFor(String typeId, Map<String, dynamic> itemBody) {
      final raw = slotQty[typeId];
      final explicit = raw is num
          ? raw.toDouble()
          : double.tryParse(raw?.toString() ?? '');
      if (explicit != null && explicit > 0) return explicit;
      final legacy = itemBody['qty'];
      final legacyQty = legacy is num
          ? legacy.toDouble()
          : double.tryParse(legacy?.toString() ?? '') ?? 1.0;
      return legacyQty <= 0 ? 1.0 : legacyQty;
    }

    var count = 0;
    var total = 0.0;
    for (final entry in slots.entries) {
      final slotValue = entry.value?.toString();
      if (slotValue == null || slotValue.isEmpty) continue;
      count += 1;
      // WAVE10: слот хранит найденную группу (found_groups) — цена её «лица».
      // Легаси-слот (equipment_items → offer) поддержан ниже.
      final candidate = seeds.itemById(slotValue);
      final candidateBody = candidate is Map && candidate['body'] is Map
          ? Map<String, dynamic>.from(candidate['body'] as Map)
          : <String, dynamic>{};
      if (candidateBody.containsKey('face_price') ||
          candidateBody.containsKey('match_kind')) {
        final priceRaw = candidateBody['face_price'];
        final price = priceRaw is num
            ? priceRaw.toDouble()
            : double.tryParse(priceRaw?.toString() ?? '') ?? 0.0;
        total += price * qtyFor(entry.key.toString(), const {});
        continue;
      }
      final itemBody = candidateBody;
      final qty = qtyFor(entry.key.toString(), itemBody);
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
      total += price * qty;
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

/// Singleton-row preference fields above a collection (e.g. MCP zip on databases list).
/// True when ui_json.poll_while is configured and at least one row
/// currently matches (e.g. any catalogs row with status=indexing).
bool _hasActivePoll(
  Map<String, dynamic> uiJson,
  List<dynamic> rows,
  dynamic seeds,
) {
  final config = parsePollWhile(uiJson['poll_while']);
  if (config == null) return false;
  for (final row in rows) {
    final id = row is Map ? row['row_id'] : row.id;
    final item = seeds.itemById(id?.toString() ?? '');
    final body = item is Map ? item['body'] : null;
    if (body is Map && config.matches(body[config.field])) return true;
  }
  return false;
}

/// `ui_json.on_load: {action}` — идентификатор экшена, тихо вызываемого один
/// раз при открытии таблицы (WAVE7: сверка «Найденных товаров» с OpenSearch).
String? _onLoadAction(Map<String, dynamic> uiJson) {
  final onLoad = uiJson['on_load'];
  if (onLoad is Map) {
    final actionId = onLoad['action']?.toString() ?? '';
    if (actionId.isNotEmpty) return actionId;
  }
  return null;
}

/// Invisible one-shot runner: invokes the on_load action quietly (no
/// snackbars) and reloads rows so the table shows refreshed data.
class _CollectionOnLoad extends StatefulWidget {
  const _CollectionOnLoad({
    required this.seeds,
    required this.actionId,
  });

  final dynamic seeds;
  final String actionId;

  @override
  State<_CollectionOnLoad> createState() => _CollectionOnLoadState();
}

class _CollectionOnLoadState extends State<_CollectionOnLoad> {
  bool _started = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _run());
  }

  Future<void> _run() async {
    if (_started || !mounted) return;
    _started = true;
    final controller = switch (widget.seeds) {
      CabinetDataController c => c,
      RuntimeDataAdapter a => a.controller,
      _ => null,
    };
    if (controller == null) return;
    try {
      await controller.invokeAction(widget.actionId);
      await controller.loadAll();
    } catch (_) {
      // Тихая сверка при открытии таблицы: ошибки не мешают просмотру данных.
    }
  }

  @override
  Widget build(BuildContext context) => const SizedBox.shrink();
}

/// Invisible poller: reloads `seeds.loadAll()` on the ui_json.poll_while
/// interval while any row matches. The surrounding ListenableBuilder then
/// rebuilds with fresh rows (live indexing progress). 30-minute hard cap.
class _CollectionPoller extends StatefulWidget {
  const _CollectionPoller({
    required this.seeds,
    required this.uiJson,
    required this.rows,
  });

  final dynamic seeds;
  final Map<String, dynamic> uiJson;
  final List<dynamic> rows;

  @override
  State<_CollectionPoller> createState() => _CollectionPollerState();
}

class _CollectionPollerState extends State<_CollectionPoller> {
  Timer? _timer;
  DateTime? _startedAt;

  @override
  void initState() {
    super.initState();
    _schedule();
  }

  @override
  void didUpdateWidget(covariant _CollectionPoller oldWidget) {
    super.didUpdateWidget(oldWidget);
    _schedule();
  }

  @override
  void dispose() {
    _timer?.cancel();
    _timer = null;
    super.dispose();
  }

  void _schedule() {
    final config = parsePollWhile(widget.uiJson['poll_while']);
    final active =
        config != null && _hasActivePoll(widget.uiJson, widget.rows, widget.seeds);
    if (!active) {
      _timer?.cancel();
      _timer = null;
      _startedAt = null;
      return;
    }
    _startedAt ??= DateTime.now();
    _timer ??= Timer.periodic(config.interval, (_) => _tick());
  }

  Future<void> _tick() async {
    if (!mounted) return;
    final started = _startedAt;
    if (started != null &&
        DateTime.now().difference(started) > const Duration(minutes: 30)) {
      _timer?.cancel();
      _timer = null;
      return;
    }
    try {
      final reload = widget.seeds.loadAll;
      if (reload is Future Function()) {
        await reload();
      } else if (reload is Function()) {
        final result = reload();
        if (result is Future) await result;
      }
    } catch (_) {}
  }

  @override
  Widget build(BuildContext context) => const SizedBox.shrink();
}

class _CollectionListHeader extends StatefulWidget {
  const _CollectionListHeader({
    required this.headerConfig,
    required this.seeds,
    required this.manifest,
    required this.readOnly,
  });

  final Map<String, dynamic> headerConfig;
  final dynamic seeds;
  final ModuleMetaManifest manifest;
  final bool readOnly;

  @override
  State<_CollectionListHeader> createState() => _CollectionListHeaderState();
}

class _CollectionListHeaderState extends State<_CollectionListHeader> {
  String? _rowId;
  bool _ensuring = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _ensureRow());
  }

  @override
  void didUpdateWidget(covariant _CollectionListHeader oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.seeds != widget.seeds ||
        oldWidget.headerConfig != widget.headerConfig) {
      WidgetsBinding.instance.addPostFrameCallback((_) => _ensureRow());
    }
  }

  Future<void> _ensureRow() async {
    if (!mounted || _ensuring) return;
    final tableSlug = widget.headerConfig['table_slug'] as String? ?? '';
    if (tableSlug.isEmpty) return;
    final existing = widget.seeds.itemsForTable(tableSlug) as List;
    if (existing.isNotEmpty) {
      final id = existing.first['row_id']?.toString();
      if (id != null && id.isNotEmpty && id != _rowId) {
        setState(() => _rowId = id);
      }
      return;
    }
    _ensuring = true;
    try {
      final ensure = widget.headerConfig['ensure_row'];
      final body = <String, dynamic>{
        if (ensure is Map) ...Map<String, dynamic>.from(ensure),
      };
      final adapter = widget.seeds;
      String? createdId;
      if (adapter is RuntimeDataAdapter) {
        createdId = await adapter.createRowAsync(tableSlug, initial: body);
      } else if (adapter is CabinetDataController) {
        createdId = await adapter.createRow(tableSlug, initial: body);
      } else if (adapter is OwnerModuleDataController) {
        createdId = await adapter.createRow(tableSlug, initial: body);
      } else {
        createdId = adapter.createRow(tableSlug) as String?;
        if (createdId != null) {
          final upsert = adapter.upsertBody(createdId, body);
          if (upsert is Future) await upsert;
        }
      }
      if (!mounted) return;
      if (createdId != null && createdId.isNotEmpty) {
        setState(() => _rowId = createdId);
      }
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    } finally {
      _ensuring = false;
    }
  }

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable:
          widget.seeds is Listenable ? widget.seeds as Listenable : ValueNotifier(0),
      builder: (context, _) {
        final tableSlug = widget.headerConfig['table_slug'] as String? ?? '';
        final fieldsRaw = widget.headerConfig['fields'];
        if (tableSlug.isEmpty || fieldsRaw is! List) {
          return const SizedBox.shrink();
        }
        var rowId = _rowId;
        if (rowId == null || rowId.isEmpty) {
          final items = widget.seeds.itemsForTable(tableSlug) as List;
          if (items.isNotEmpty) {
            rowId = items.first['row_id']?.toString();
          }
        }
        if (rowId == null || rowId.isEmpty) {
          return const SizedBox.shrink();
        }
        final l10n = AppLocalizations.of(context);
        final locale = Localizations.localeOf(context);
        final body = Map<String, dynamic>.from(
          widget.seeds.bodyFor(rowId) as Map? ?? const {},
        );
        final columns = widget.manifest.columnsForTable(tableSlug);
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
      },
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
        final patch = widget.seeds.patchField(rowId, columnName, next);
        if (patch is Future) await patch;
      } catch (e) {
        if (context.mounted) AppErrors.showSnack(context, e);
      }
    }

    if (widgetKind == 'file_upload') {
      final scope = ModuleRuntimeScope.maybeOf(context);
      if (scope == null) {
        return ListTile(
          title: Text(label),
          subtitle: const Text('Upload unavailable (no module scope)'),
        );
      }
      return FileUploadField(
        label: label.isNotEmpty ? label : columnName,
        value: value,
        scope: scope,
        readOnly: widget.readOnly,
        accept: fieldCfg['accept'] as String?,
        // No subtitle — warning chrome only when empty.
        subtitle: null,
        warnWhenEmpty: fieldCfg['empty_style']?.toString() == 'warning',
        onChanged: persist,
      );
    }

    if (widgetKind == 'project_multiselect') {
      return ProjectMultiselectField(
        label: label,
        value: value,
        readOnly: widget.readOnly,
        subtitleMode: fieldCfg['subtitle']?.toString(),
        onChanged: (ids) => persist(ids),
      );
    }

    return AppValuePreference<String>(
      title: label.isNotEmpty ? label : columnName,
      value: value?.toString() ?? '',
      icon: Icons.badge_outlined,
      enabled: !widget.readOnly,
      onSave: (v) async {
        final colType = col?['type']?.toString();
        if (colType == 'number') {
          final trimmed = v.trim();
          if (trimmed.isEmpty) {
            await persist(null);
            return;
          }
          final asInt = int.tryParse(trimmed);
          if (asInt != null) {
            await persist(asInt);
            return;
          }
          final asDouble = double.tryParse(trimmed);
          await persist(asDouble ?? trimmed);
          return;
        }
        await persist(v.trim());
      },
    );
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
      onSave: (v) async {
        final colType = col?['type']?.toString();
        if (colType == 'number') {
          final trimmed = v.trim();
          if (trimmed.isEmpty) {
            await persist(null);
            return;
          }
          final asInt = int.tryParse(trimmed);
          if (asInt != null) {
            await persist(asInt);
            return;
          }
          final asDouble = double.tryParse(trimmed);
          await persist(asDouble ?? trimmed);
          return;
        }
        await persist(v.trim());
      },
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

/// Tap-to-toggle bool cell for `editable: true` columns whose body value is a
/// bool (WAVE7: include_delivery в «Закупке»). Mirrors EditableNumberCell:
/// looks like a plain cell, tap flips the value via patchField.
class _EditableBoolCell extends StatelessWidget {
  const _EditableBoolCell({
    required this.value,
    required this.onToggle,
  });

  final bool value;
  final Future<void> Function() onToggle;

  @override
  Widget build(BuildContext context) {
    final colors = context.appColors;
    return Align(
      alignment: AlignmentDirectional.centerEnd,
      child: InkWell(
        borderRadius: BorderRadius.circular(6),
        onTap: onToggle,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 2),
          child: Icon(
            value ? Icons.check_box : Icons.check_box_outline_blank,
            size: 20,
            color: value ? colors.success : colors.muted,
          ),
        ),
      ),
    );
  }
}
