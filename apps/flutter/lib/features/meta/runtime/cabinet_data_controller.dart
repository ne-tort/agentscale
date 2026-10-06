import 'package:flutter/foundation.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/features/meta/module_cell_format.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/features/meta/runtime/module_pick_context.dart';

typedef ProjectsRematerializeCallback = void Function(int scheduled, {required bool inline});
typedef WorkspaceOutdatedCallback = void Function();

/// Live cabinet / project-instance module data — mirrors SeedDataController API for interpreters.
class CabinetDataController extends ChangeNotifier with ModulePickContextMixin {
  CabinetDataController({
    required this.api,
    required this.cabinetId,
    required this.moduleId,
    required ModuleMetaManifest manifest,
    this.projectId,
    this.sessionId,
  }) : _manifest = manifest;

  final ProdavanApi api;
  final String cabinetId;
  final String? projectId;
  final String? sessionId;
  final String moduleId;
  ModuleMetaManifest _manifest;
  final List<Map<String, dynamic>> _items = [];

  bool get _useProjectInstance => projectId != null && projectId!.isNotEmpty;

  ProjectsRematerializeCallback? onProjectsRematerialize;
  WorkspaceOutdatedCallback? onWorkspaceOutdated;

  ModuleMetaManifest get manifest => _manifest;

  List<Map<String, dynamic>> get items => List.unmodifiable(_items);

  /// Cross-chat («Закупка») rows per table: views with
  /// `ui_json.data_scope.chats == 'all'` read these instead of the
  /// session-scoped [_items]. Loaded lazily via [ensureCrossChat] and
  /// refreshed together with the regular data in [loadAll].
  final Map<String, List<Map<String, dynamic>>> _crossChatItems = {};
  final Set<String> _crossChatRequested = {};
  final Set<String> _crossChatLoading = {};

  /// Whether [tableSlug] needs project-wide rows (any view opted in).
  bool tableNeedsCrossChat(String tableSlug) {
    for (final view in _manifest.views) {
      if (view['table_slug'] != tableSlug) continue;
      final ui = view['ui_json'];
      if (ui is Map) {
        final scope = ui['data_scope'];
        if (scope is Map && scope['chats'] == 'all') return true;
      }
    }
    return false;
  }

  /// Ensure project-wide rows for [tableSlug] are loading/loaded (no-op in
  /// the cabinet contour — no chats there). One-shot per table: дальнейшие
  /// обновления приезжают через [loadAll] → [_reloadCrossChat].
  Future<void> ensureCrossChat(String tableSlug) async {
    if (!_useProjectInstance) return;
    if (_crossChatItems.containsKey(tableSlug)) return;
    if (_crossChatLoading.contains(tableSlug)) return;
    _crossChatRequested.add(tableSlug);
    _crossChatLoading.add(tableSlug);
    try {
      await _loadCrossChat(tableSlug);
    } finally {
      _crossChatLoading.remove(tableSlug);
    }
  }

  Future<void> _loadCrossChat(String tableSlug) async {
    final rows = await api.listProjectRuntimeModuleDataRows(
      projectId: projectId!,
      moduleId: moduleId,
      tableSlug: tableSlug,
      chatsAll: true,
    );
    _crossChatItems[tableSlug] = [
      for (final row in rows)
        {
          'table_slug': tableSlug,
          'row_id': row['row_id'],
          // session_id самой строки — запись (правка/выбор) идёт в её чат.
          'session_id': row['session_id']?.toString(),
          'body': row['body'] is Map
              ? Map<String, dynamic>.from(row['body'] as Map)
              : <String, dynamic>{},
        },
    ];
    notifyListeners();
  }

  Future<void> loadAll() async {
    final next = <Map<String, dynamic>>[];
    for (final table in _manifest.tables) {
      final slug = table['slug'] as String?;
      if (slug == null || slug.isEmpty) continue;
      final rows = _useProjectInstance
          ? await api.listProjectRuntimeModuleDataRows(
              projectId: projectId!,
              moduleId: moduleId,
              tableSlug: slug,
              sessionId: sessionId,
            )
          : await api.listModuleDataRows(
              cabinetId: cabinetId,
              moduleId: moduleId,
              tableSlug: slug,
              sessionId: sessionId,
            );
      for (final row in rows) {
        next.add({
          'table_slug': slug,
          'row_id': row['row_id'],
          'session_id': row['session_id']?.toString(),
          'body': row['body'] is Map
              ? Map<String, dynamic>.from(row['body'] as Map)
              : <String, dynamic>{},
        });
      }
    }
    if (_itemsFingerprint(next) == _itemsFingerprint(_items)) {
      // Сессионные данные не изменились — но кросс-чатовые таблицы могли.
      await _reloadCrossChat();
      return;
    }
    _items
      ..clear()
      ..addAll(next);
    notifyListeners();
    await _reloadCrossChat();
  }

  Future<void> _reloadCrossChat() async {
    for (final tableSlug in _crossChatRequested.toList()) {
      if (_crossChatLoading.contains(tableSlug)) continue;
      _crossChatLoading.add(tableSlug);
      try {
        await _loadCrossChat(tableSlug);
      } catch (_) {
        // best-effort: кросс-чатовая подгрузка не должна ломать экран
      } finally {
        _crossChatLoading.remove(tableSlug);
      }
    }
  }

  String _itemsFingerprint(List<Map<String, dynamic>> items) {
    // Cheap structural fingerprint so silent polls skip rebuild when unchanged.
    final buf = StringBuffer();
    for (final item in items) {
      buf
        ..write(item['table_slug'])
        ..write('|')
        ..write(item['row_id'])
        ..write('|')
        ..write(item['body'])
        ..write(';');
    }
    return buf.toString();
  }

  List<Map<String, dynamic>> itemsForTable(String tableSlug) {
    return _items.where((i) => i['table_slug'] == tableSlug).toList();
  }

  Map<String, dynamic>? itemById(String rowId) {
    for (final item in _items) {
      if (item['row_id'] == rowId) return item;
    }
    // Кросс-чатовые строки (Закупка → товары поставщика): выбор/правка
    // находятся по row_id и здесь.
    for (final rows in _crossChatItems.values) {
      for (final item in rows) {
        if (item['row_id'] == rowId) return item;
      }
    }
    return null;
  }

  Map<String, dynamic> bodyFor(String rowId) {
    final item = itemById(rowId);
    final body = item?['body'];
    if (body is Map) return Map<String, dynamic>.from(body);
    return {};
  }

  void _emitRematerialize(Map<String, dynamic>? payload) {
    final remat = payload?['rematerialize'] ?? payload?['workspace_sync'];
    if (remat is Map) {
      final marked = remat['marked_outdated'];
      if (marked is int && marked > 0) {
        onWorkspaceOutdated?.call();
        return;
      }
      final scheduled = remat['scheduled'];
      if (scheduled is int && scheduled > 0) {
        final sync = remat['sync'];
        final inline = sync is List && sync.isNotEmpty;
        onProjectsRematerialize?.call(scheduled, inline: inline);
        return;
      }
      // mode=deferred без отметок (no-op запись, таблица вне workspace,
      // sync_failed) — НЕ повод для баннера «Проект требует обновления».
    }
    final outdated = payload?['workspace_outdated'];
    if (outdated is Map && (outdated['marked_outdated'] as int? ?? 0) > 0) {
      onWorkspaceOutdated?.call();
    }
  }

  Future<String> createRow(String tableSlug, {Map<String, dynamic>? initial}) async {
    final body = initial ?? defaultBodyForTable(tableSlug);
    final created = _useProjectInstance
        ? await api.createProjectRuntimeModuleDataRow(
            projectId: projectId!,
            moduleId: moduleId,
            tableSlug: tableSlug,
            body: body,
            sessionId: sessionId,
          )
        : await api.createModuleDataRow(
            cabinetId: cabinetId,
            moduleId: moduleId,
            tableSlug: tableSlug,
            body: body,
            sessionId: sessionId,
          );
    _emitRematerialize(created);
    final rowId = created['row_id'] as String;
    _items.add({
      'table_slug': tableSlug,
      'row_id': rowId,
      'body': Map<String, dynamic>.from(created['body'] as Map? ?? body),
    });
    notifyListeners();
    return rowId;
  }

  Future<void> upsertBody(String rowId, Map<String, dynamic> body) async {
    final item = itemById(rowId);
    if (item == null) return;
    final tableSlug = item['table_slug'] as String;
    // Строка из другого чата (кросс-чатовая Закупка) пишется в свой бакет.
    final rowSession = item['session_id'] as String?;
    final effectiveSession =
        (rowSession != null && rowSession.isNotEmpty) ? rowSession : sessionId;
    final updated = _useProjectInstance
        ? await api.updateProjectRuntimeModuleDataRow(
            projectId: projectId!,
            moduleId: moduleId,
            tableSlug: tableSlug,
            rowId: rowId,
            body: body,
            sessionId: effectiveSession,
          )
        : await api.updateModuleDataRow(
            cabinetId: cabinetId,
            moduleId: moduleId,
            tableSlug: tableSlug,
            rowId: rowId,
            body: body,
            sessionId: effectiveSession,
          );
    _emitRematerialize(updated);
    final updatedBody = updated['body'];
    _replaceItem(
      rowId,
      updatedBody is Map ? Map<String, dynamic>.from(updatedBody) : body,
    );
    notifyListeners();
  }

  void _replaceItem(String rowId, Map<String, dynamic> body) {
    final nextBody = Map<String, dynamic>.from(body);
    for (var i = 0; i < _items.length; i++) {
      if (_items[i]['row_id'] == rowId) {
        _items[i] = {..._items[i], 'body': nextBody};
        return;
      }
    }
    for (final rows in _crossChatItems.values) {
      for (var i = 0; i < rows.length; i++) {
        if (rows[i]['row_id'] == rowId) {
          rows[i] = {...rows[i], 'body': nextBody};
          return;
        }
      }
    }
  }

  Future<void> patchField(String rowId, String field, dynamic value) async {
    final body = bodyFor(rowId);
    body[field] = value;
    await upsertBody(rowId, body);
  }

  Future<void> deleteRow(String rowId) async {
    final item = itemById(rowId);
    if (item == null) {
      throw StateError('module data row not found: $rowId');
    }
    final rowSession = item['session_id'] as String?;
    final effectiveSession =
        (rowSession != null && rowSession.isNotEmpty) ? rowSession : sessionId;
    final deleted = _useProjectInstance
        ? await api.deleteProjectRuntimeModuleDataRow(
            projectId: projectId!,
            moduleId: moduleId,
            tableSlug: item['table_slug'] as String,
            rowId: rowId,
            sessionId: effectiveSession,
          )
        : await api.deleteModuleDataRow(
            cabinetId: cabinetId,
            moduleId: moduleId,
            tableSlug: item['table_slug'] as String,
            rowId: rowId,
            sessionId: effectiveSession,
          );
    _emitRematerialize(deleted);
    _items.removeWhere((i) => i['row_id'] == rowId);
    for (final rows in _crossChatItems.values) {
      rows.removeWhere((i) => i['row_id'] == rowId);
    }
    notifyListeners();
  }

  /// Invokes a module action and returns the raw result body (may carry
  /// `file_ref` for exports). Reloads data afterwards so the UI reflects
  /// any server-side row updates.
  Future<Map<String, dynamic>> invokeAction(String actionId, {String? rowId}) async {
    final result = await api.invokeModuleAction(
      cabinetId: cabinetId,
      moduleId: moduleId,
      actionId: actionId,
      rowId: rowId,
      projectId: projectId,
      sessionId: sessionId,
    );
    await loadAll();
    return result;
  }

  void refresh() => notifyListeners();

  Map<String, dynamic> defaultBodyForTable(String tableSlug) {
    final body = <String, dynamic>{};
    for (final c in _manifest.columnsForTable(tableSlug)) {
      final name = c['name'] as String? ?? '';
      if (name.isEmpty) continue;
      if (c.containsKey('default')) {
        body[name] = c['default'];
      }
    }
    return body;
  }

  List<AppEntityRow> entityRows(String tableSlug, Map<String, dynamic> uiJson) {
    final titleField = uiJson['title_field'] as String? ?? 'name';
    final subtitleFields = uiJson['subtitle_fields'] is List
        ? (uiJson['subtitle_fields'] as List).cast<String>()
        : <String>[];
    final columnDefs = uiJson['columns'] is List
        ? (uiJson['columns'] as List)
            .whereType<Map>()
            .map((c) => Map<String, dynamic>.from(c))
            .toList()
        : <Map<String, dynamic>>[];

    // Кросс-чатовая вьюха (Закупка → товары поставщика): строки всех чатов
    // проекта, пока они загружены; до первой загрузки — сессионный список.
    final dataScope = uiJson['data_scope'];
    final wantsCrossChat =
        dataScope is Map && dataScope['chats'] == 'all';
    final dataRows = wantsCrossChat && _crossChatItems.containsKey(tableSlug)
        ? _crossChatItems[tableSlug]!
        : itemsForTable(tableSlug);
    return dataRows.map((item) {
      final rowId = item['row_id'] as String? ?? '';
      final body = item['body'] is Map
          ? Map<String, dynamic>.from(item['body'] as Map)
          : <String, dynamic>{};
      final title = body[titleField]?.toString() ?? rowId;
      final subtitle = subtitleFields
          .map((f) {
            final metaCol = _manifest.columnsForTable(tableSlug).cast<Map<String, dynamic>?>().firstWhere(
                  (c) => c?['name'] == f,
                  orElse: () => null,
                );
            if (metaCol != null && metaCol['type']?.toString() == 'ref') {
              return formatModuleCell(
                item: item,
                body: body,
                col: {
                  'field': f,
                  'type': metaCol['type'],
                  'ref': metaCol['ref'],
                },
                tableSlug: tableSlug,
                itemsForTable: itemsForTable,
              );
            }
            return body[f]?.toString();
          })
          .whereType<String>()
          .where((s) => s.isNotEmpty)
          .join(' · ');
      final cells = <String, String>{};
      for (final col in columnDefs) {
        final field = col['field'] as String? ?? '';
        if (field.isEmpty) continue;
        final merged = Map<String, dynamic>.from(col);
        final metaCol = _manifest.columnsForTable(tableSlug).cast<Map<String, dynamic>?>().firstWhere(
              (c) => c?['name'] == field,
              orElse: () => null,
            );
        if (metaCol != null) {
          if (metaCol['enum'] is Map && merged['enum'] == null) {
            merged['enum'] = metaCol['enum'];
          }
          merged['type'] ??= metaCol['type'];
          merged['ref'] ??= metaCol['ref'];
        }
        cells[field] = _cellValue(item, body, merged);
      }
      return AppEntityRow(
        id: rowId,
        title: title.isEmpty ? rowId : title,
        subtitle: subtitle.isEmpty ? null : subtitle,
        cells: cells,
      );
    }).toList();
  }

  String _cellValue(
    Map<String, dynamic> item,
    Map<String, dynamic> body,
    Map<String, dynamic> col,
  ) {
    return formatModuleCell(
      item: item,
      body: body,
      col: col,
      tableSlug: item['table_slug']?.toString() ?? '',
      itemsForTable: itemsForTable,
    );
  }
}
