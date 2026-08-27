import 'package:flutter/material.dart';

import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_icon_button.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/features/meta/preview/preview_stub.dart';
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
        final columns = _columns(uiJson, l10n);
        final rows = _filteredRows(seeds, tableSlug, uiJson);
        final toolbar = _toolbar(context, uiJson, tableSlug, l10n);
        final emptyUi = uiJson['empty'];
        final emptyTitle = emptyUi is Map
            ? emptyUi['title'] as String? ?? l10n.adminModuleSeedEmpty
            : l10n.adminModuleSeedEmpty;

        final inline = _inlineAdd(context, uiJson, tableSlug);
        final collection = AppEntityCollection(
          rows: rows,
          columns: columns,
          primaryColumnLabel: columns.isNotEmpty ? columns.first.label : l10n.commonEntity,
          toolbar: toolbar,
          empty: EmptyPlaceholder(
            title: emptyTitle,
            fillViewport: false,
            action: !readOnly && _createLabel(uiJson) != null
                ? TextButton(
                    onPressed: () => _create(context, uiJson, tableSlug),
                    child: Text(_createLabel(uiJson)!),
                  )
                : null,
          ),
          onOpen: (row) {
            final rowTap = uiJson['row_tap'];
            if (rowTap is Map && rowTap['kind'] == 'open_form') {
              final formView = rowTap['view'] as String?;
              if (formView != null && onOpenForm != null) {
                onOpenForm!(formView, rowId: row.id);
                return;
              }
            }
          },
          onDelete: readOnly
              ? null
              : (row) async {
                  seeds.deleteRow(row.id);
                },
        );
        if (inline == null) return collection;
        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [inline, Expanded(child: collection)],
        );
      },
    );
  }

  List<AppEntityRow> _filteredRows(dynamic seeds, String tableSlug, Map<String, dynamic> uiJson) {
    final all = seeds.entityRows(tableSlug, uiJson) as List<AppEntityRow>;
    final filter = uiJson['row_filter'];
    if (filter is! Map) return all;
    final contextBind = uiJson['context_bind'];
    String? profileId = contextRowId;
    if (contextBind is Map && contextBind['profile_id'] == 'contextRowId') {
      profileId = contextRowId;
    }
    return all.where((row) {
      final item = seeds.itemById(row.id);
      final body = item?['body'];
      if (body is! Map) return false;
      for (final entry in filter.entries) {
        if (body[entry.key]?.toString() != entry.value.toString()) return false;
      }
      if (profileId != null && body['profile_id']?.toString() != profileId) return false;
      return true;
    }).toList();
  }

  Widget? _inlineAdd(BuildContext context, Map<String, dynamic> uiJson, String tableSlug) {
    if (readOnly) return null;
    final inline = uiJson['inline_add'];
    if (inline is! Map) return null;
    final field = inline['field'] as String? ?? 'name';
    final controller = TextEditingController();
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
      child: Row(
        children: [
          Expanded(
            child: TextField(
              controller: controller,
              decoration: InputDecoration(
                hintText: inline['label'] as String? ?? 'Add',
                isDense: true,
              ),
            ),
          ),
          IconButton(
            icon: const Icon(Icons.add),
            onPressed: () async {
              final name = controller.text.trim();
              if (name.isEmpty) return;
              final body = seeds.defaultBodyForTable(tableSlug);
              body[field] = name;
              final filter = uiJson['row_filter'];
              if (filter is Map) {
                body.addAll(Map<String, dynamic>.from(filter));
              }
              if (contextRowId != null) {
                body['profile_id'] = contextRowId;
              }
              if (seeds.runtimeType.toString().contains('RuntimeDataAdapter')) {
                await seeds.createRowAsync(tableSlug, initial: body);
              } else {
                final rowId = seeds.createRow(tableSlug);
                seeds.upsertBody(rowId, body);
              }
              controller.clear();
            },
          ),
        ],
      ),
    );
  }

  List<AppEntityColumn> _columns(Map<String, dynamic> uiJson, AppLocalizations l10n) {
    final raw = uiJson['columns'];
    if (raw is! List) return const [];
    return raw.whereType<Map>().map((c) {
      return AppEntityColumn(
        id: c['field'] as String? ?? '',
        label: c['label'] as String? ?? c['field'] as String? ?? '',
        width: c['width'] is num ? (c['width'] as num).toDouble() : null,
      );
    }).where((c) => c.id.isNotEmpty).toList();
  }

  List<Widget>? _toolbar(
    BuildContext context,
    Map<String, dynamic> uiJson,
    String tableSlug,
    AppLocalizations l10n,
  ) {
    final items = <Widget>[];
    final primary = uiJson['primary_action'];
    if (!readOnly && primary is Map && primary['kind'] == 'create_row') {
      items.add(
        AppIconButton(
          icon: Icons.add,
          tooltip: _createLabel(uiJson) ?? 'Add',
          onPressed: () => _create(context, uiJson, tableSlug),
        ),
      );
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
          items.add(
            AppIconButton(
              icon: Icons.bolt_outlined,
              tooltip: t['action'] as String? ?? 'Action',
              onPressed: () => PreviewStub.run(context, t['action'] as String? ?? 'Action'),
            ),
          );
        }
      }
    }
    return items.isEmpty ? null : items;
  }

  String? _createLabel(Map<String, dynamic> uiJson) {
    final primary = uiJson['primary_action'];
    if (primary is Map) {
      return primary['label'] as String? ?? primary['kind'] as String?;
    }
    final empty = uiJson['empty'];
    if (empty is Map) {
      final action = empty['action'];
      if (action is Map) return action['label'] as String?;
    }
    return null;
  }

  void _create(BuildContext context, Map<String, dynamic> uiJson, String tableSlug) {
    final rowId = seeds.createRow(tableSlug);
    final rowTap = uiJson['row_tap'];
    if (rowTap is Map && rowTap['kind'] == 'open_form') {
      final formView = rowTap['view'] as String?;
      if (formView != null && onOpenForm != null) {
        onOpenForm!(formView, rowId: rowId);
      }
    }
  }
}
