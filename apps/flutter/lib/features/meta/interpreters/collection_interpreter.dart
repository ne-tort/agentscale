import 'package:flutter/material.dart';

import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_icon_button.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/features/meta/preview/preview_data_store.dart';
import 'package:prodavan/features/meta/preview/preview_stub.dart';
import 'package:prodavan/l10n/app_localizations.dart';

class CollectionViewInterpreter extends StatelessWidget {
  const CollectionViewInterpreter({
    super.key,
    required this.manifest,
    required this.view,
    this.onOpenForm,
  });

  final ModuleMetaManifest manifest;
  final Map<String, dynamic> view;
  final void Function(String viewSlug)? onOpenForm;

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

    final columns = _columns(uiJson, l10n);
    final rows = PreviewDataStore.entityRows(manifest, tableSlug, uiJson);
    final toolbar = _toolbar(context, uiJson);
    final emptyUi = uiJson['empty'];
    final emptyTitle = emptyUi is Map
        ? emptyUi['title'] as String? ?? l10n.commonEmpty
        : l10n.commonEmpty;

    return AppEntityCollection(
      rows: rows,
      columns: columns,
      primaryColumnLabel: columns.isNotEmpty ? columns.first.label : l10n.commonEntity,
      toolbar: toolbar,
      empty: EmptyPlaceholder(
        title: emptyTitle,
        fillViewport: false,
        action: _createLabel(uiJson) != null
            ? TextButton(
                onPressed: () => _stubCreate(context, uiJson),
                child: Text(_createLabel(uiJson)!),
              )
            : null,
      ),
      onOpen: (row) {
        final rowTap = uiJson['row_tap'];
        if (rowTap is Map && rowTap['kind'] == 'open_form') {
          final formView = rowTap['view'] as String?;
          if (formView != null && onOpenForm != null) {
            onOpenForm!(formView);
            return;
          }
        }
        PreviewStub.run(context, row.title);
      },
      onDelete: (row) async {
        PreviewStub.run(context, l10n.commonDelete);
      },
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

  List<Widget>? _toolbar(BuildContext context, Map<String, dynamic> uiJson) {
    final items = <Widget>[];
    final primary = uiJson['primary_action'];
    if (primary is Map && primary['kind'] == 'create_row') {
      items.add(
        AppIconButton(
          icon: Icons.add,
          tooltip: _createLabel(uiJson) ?? 'Add',
          onPressed: () => _stubCreate(context, uiJson),
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
              onPressed: () => PreviewStub.run(context, 'Refresh'),
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

  void _stubCreate(BuildContext context, Map<String, dynamic> uiJson) {
    final primary = uiJson['primary_action'];
    if (primary is Map && primary['kind'] == 'create_row') {
      PreviewStub.run(context, primary['label'] as String? ?? 'Add');
    }
  }
}
