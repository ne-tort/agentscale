import 'package:flutter/material.dart';

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
import 'package:prodavan/features/meta/runtime/runtime_data_adapter.dart';
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
        final rows = _withSelection(context, uiJson, rawRows);
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
                  seeds.deleteRow(row.id);
                },
        );

        if (!hasInline) return collection;

        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
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

  List<AppEntityRow> _filteredRows(dynamic seeds, String tableSlug, Map<String, dynamic> uiJson) {
    final all = seeds.entityRows(tableSlug, uiJson) as List<AppEntityRow>;
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
    if (filter is! Map && bindEntries.isEmpty) return all;
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
      return true;
    }).toList();
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
      return AppEntityRow(
        id: row.id,
        title: row.title,
        subtitle: row.subtitle,
        cells: row.cells,
        cellWidgets: row.cellWidgets,
        titleColor: row.titleColor,
        rowColor: row.rowColor,
        titleBold: row.titleBold,
        trailing: row.trailing,
        leading: Radio<String>(
          value: row.id,
          groupValue: selectedId,
          onChanged: readOnly || actionId.isEmpty
              ? null
              : (_) => _invokeAction(context, actionId, rowId: row.id),
        ),
      );
    }).toList();
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
