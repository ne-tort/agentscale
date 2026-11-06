import 'package:flutter/foundation.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/session/chat_open_intent.dart';
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

  /// Server-paged virtual tables (ui_json.server_paged): rows come from
  /// OpenSearch one page at a time; [_serverPages] holds pager state.
  final Map<String, _ServerPageState> _serverPages = {};

  bool isServerPaged(String tableSlug) {
    for (final view in _manifest.views) {
      if (view['table_slug'] != tableSlug) continue;
      final ui = view['ui_json'];
      if (ui is Map && ui['server_paged'] == true) return true;
    }
    return false;
  }

  int serverPage(String tableSlug) => _serverPages[tableSlug]?.page ?? 1;

  int serverPageSize(String tableSlug) => _serverPages[tableSlug]?.pageSize ?? 50;

  int serverTotal(String tableSlug) => _serverPages[tableSlug]?.total ?? 0;

  /// Readiness of owner-shared equipment catalogs used by employee search.
  /// Derived from rows loaded through the existing employee/project SoT read.
  String equipmentSearchReadiness() {
    final catalogs = itemsForTable('catalogs').where((item) {
      if (!_useProjectInstance) return true;
      final body = item['body'];
      if (body is! Map) return false;
      final projectIds = body['project_ids'];
      return projectIds is! List ||
          projectIds.isEmpty ||
          projectIds.map((id) => id.toString()).contains(projectId);
    }).toList();
    if (catalogs.isEmpty) return 'no_catalogs';
    var hasPaused = false;
    var hasIndexing = false;
    var hasQueued = false;
    var hasError = false;
    var hasReady = false;
    for (final item in catalogs) {
      final body = item['body'];
      if (body is! Map) continue;
      final status = body['status']?.toString().trim().toLowerCase();
      final paused = body['paused'] == true;
      if (paused) hasPaused = true;
      if (status == 'ready' && !paused) hasReady = true;
      if (status == 'indexing') hasIndexing = true;
      if (status == 'queued') hasQueued = true;
      if (status == 'error') hasError = true;
    }
    if (hasReady) return 'ready';
    if (hasIndexing) return 'indexing';
    if (hasQueued) return 'queued';
    if (hasError) return 'error';
    if (hasPaused) return 'paused';
    return 'no_ready';
  }

  String serverSearch(String tableSlug) => _serverPages[tableSlug]?.search ?? '';

  bool serverStockOnly(String tableSlug) =>
      _serverPages[tableSlug]?.stockOnly ?? false;

  Future<void> setServerStockOnly(String tableSlug, bool enabled) async {
    final state = _serverPages.putIfAbsent(tableSlug, _ServerPageState.new);
    if (state.stockOnly == enabled) return;
    state.stockOnly = enabled;
    state.page = 1;
    await _loadServerPage(tableSlug);
  }

  Future<void> setServerPage(String tableSlug, int page) async {
    final state = _serverPages.putIfAbsent(tableSlug, _ServerPageState.new);
    final clamped = page < 1 ? 1 : page;
    if (clamped == state.page) return;
    state.page = clamped;
    await _loadServerPage(tableSlug);
  }

  /// Кнопка обновления в шапке страницы: перечитать текущую страницу
  /// виртуальной таблицы из OpenSearch (данные живут только там).
  Future<void> reloadServerPage(String tableSlug) async {
    if (!isServerPaged(tableSlug)) return;
    await _loadServerPage(tableSlug);
  }

  Future<void> setServerSearch(String tableSlug, String query) async {
    final state = _serverPages.putIfAbsent(tableSlug, _ServerPageState.new);
    if (query == state.search) return;
    state.search = query;
    state.page = 1;
    await _loadServerPage(tableSlug);
  }

  Map<String, dynamic>? _employeeSearchConfig(String tableSlug) {
    for (final view in _manifest.views) {
      if (view['table_slug'] != tableSlug) continue;
      final ui = view['ui_json'];
      if (ui is Map) {
        final config = ui['employee_search'];
        if (config is Map) return Map<String, dynamic>.from(config);
      }
    }
    // Current equipment metadata predates the employee Search Index route.
    // Keep a client default so the employee UI can use the generic endpoint
    // without requiring a metadata/backend migration.
    if (tableSlug == 'equipment_search') {
      return {
        'namespace': 'equipment',
        'catalog_table': 'catalogs',
        'search_fields': ['title', 'part_number', 'brand', 'supplier'],
        'sort': [{'src_hash': 'asc'}],
        'stock_field': 'in_stock',
      };
    }
    return null;
  }

  Future<void> _loadEmployeeSearchPage(String tableSlug, _ServerPageState state) async {
    // catalog metadata is loaded via the existing SoT rows endpoint, not the
    // Search Index API. Load it before deriving readiness and index names.
    if (itemsForTable('catalogs').isEmpty &&
        _manifest.tables.any((table) => table['slug'] == 'catalogs')) {
      final rows = _useProjectInstance
          ? await api.listProjectRuntimeModuleDataRows(
              projectId: projectId!,
              moduleId: moduleId,
              tableSlug: 'catalogs',
              chatsAll: true,
            )
          : await api.listModuleDataRows(
              cabinetId: cabinetId,
              moduleId: moduleId,
              tableSlug: 'catalogs',
              sessionId: sessionId,
            );
      _items.removeWhere((item) => item['table_slug'] == 'catalogs');
      _items.addAll([
        for (final row in rows)
          {
            'table_slug': 'catalogs',
            'row_id': row['row_id'],
            'body': row['body'] is Map
                ? Map<String, dynamic>.from(row['body'] as Map)
                : <String, dynamic>{},
          },
      ]);
      notifyListeners();
    }
    final config = _employeeSearchConfig(tableSlug);
    if (config == null) throw StateError('Employee search is not configured');
    final namespace = config['namespace']?.toString() ?? '';
    final index = config['index']?.toString();
    final indexes = <String>[];
    if (index != null && index.isNotEmpty) {
      indexes.add(index);
    } else {
      final catalogTable = config['catalog_table']?.toString() ?? 'catalogs';
      var catalogItems = itemsForTable(catalogTable);
      if (catalogItems.isEmpty) {
        final rows = _useProjectInstance
            ? await api.listProjectRuntimeModuleDataRows(
                projectId: projectId!, moduleId: moduleId, tableSlug: catalogTable)
            : await api.listModuleDataRows(
                cabinetId: cabinetId, moduleId: moduleId, tableSlug: catalogTable);
        catalogItems = [
          for (final row in rows)
            {'row_id': row['row_id'], 'body': row['body']},
        ];
      }
      for (final item in catalogItems) {
        final body = item['body'];
        if (body is! Map || body['status']?.toString().toLowerCase() != 'ready') continue;
        final id = item['row_id']?.toString() ?? '';
        final safe = id.toLowerCase().replaceAll(RegExp(r'[^a-z0-9_]+'), '_').replaceAll(RegExp(r'^_+|_+$'), '');
        if (safe.isEmpty) continue;
        final normalized = RegExp(r'^[a-z]').hasMatch(safe) ? safe : 'r_$safe';
        indexes.add('c_${normalized.substring(0, normalized.length.clamp(0, 62))}');
      }
    }
    if (namespace.isEmpty || indexes.isEmpty) {
      throw StateError('No employee-search indexes are available');
    }

    final searchFields = (config['search_fields'] is List)
        ? (config['search_fields'] as List).map((v) => v.toString()).where((v) => v.isNotEmpty).toList()
        : const <String>[];
    final typedQuery = _employeeSearchQuery(state.search, searchFields);
    final configuredQuery = config['query'];
    final query = configuredQuery is Map && typedQuery.containsKey('multi_match')
        ? {
            'bool': {
              'must': [
                Map<String, dynamic>.from(configuredQuery),
                typedQuery,
              ],
            },
          }
        : configuredQuery is Map
            ? Map<String, dynamic>.from(configuredQuery)
            : typedQuery;
    final filter = config['filter'] is Map
        ? Map<String, dynamic>.from(config['filter'] as Map)
        : <String, dynamic>{};
    final stockField = config['stock_field']?.toString();
    if (state.stockOnly && stockField != null && stockField.isNotEmpty) {
      filter[stockField] = true;
    }
    final sort = config['sort'] is List
        ? (config['sort'] as List).whereType<Map>().map((v) => Map<String, dynamic>.from(v)).toList()
        : const <Map<String, dynamic>>[];
    if (sort.isEmpty) throw StateError('Employee search requires deterministic sort');

    final needed = state.page * state.pageSize;
    final merged = <Map<String, dynamic>>[];
    var total = 0;
    for (final searchIndex in indexes.toSet()) {
      List<dynamic>? cursor;
      var fetched = 0;
      while (fetched < needed) {
        final size = (needed - fetched).clamp(1, 200).toInt();
        final result = await api.employeeSearchQuery(
          projectId: projectId!,
          namespace: namespace,
          index: searchIndex,
          query: query,
          filter: filter,
          sort: sort,
          searchAfter: cursor,
          size: size,
        );
        if (fetched == 0) total += result.total;
        merged.addAll(result.hits);
        fetched += result.hits.length;
        if (result.hits.length < size ||
            result.nextSearchAfter == null ||
            listEquals(result.nextSearchAfter, cursor)) {
          break;
        }
        cursor = result.nextSearchAfter;
      }
    }
    merged.sort((a, b) => _compareEmployeeSearchHits(a, b, sort));
    state.total = total;
    final start = (state.page - 1) * state.pageSize;
    final pageRows = merged.skip(start).take(state.pageSize);
    _replaceServerPage(tableSlug, pageRows.toList());
  }

  int _compareEmployeeSearchHits(
    Map<String, dynamic> left,
    Map<String, dynamic> right,
    List<Map<String, dynamic>> sort,
  ) {
    for (final spec in sort) {
      if (spec.isEmpty) continue;
      final field = spec.keys.first;
      final rawDirection = spec[field];
      final direction = rawDirection is Map
          ? rawDirection['order']?.toString().toLowerCase()
          : rawDirection?.toString().toLowerCase();
      final descending = direction == 'desc';
      final a = left[field];
      final b = right[field];
      final comparison = a == b
          ? 0
          : a == null
              ? -1
              : b == null
                  ? 1
                  : a is num && b is num
                      ? a.compareTo(b)
                      : a.toString().compareTo(b.toString());
      if (comparison != 0) return descending ? -comparison : comparison;
    }
    return 0;
  }

  Map<String, dynamic> _employeeSearchQuery(String search, List<String> fields) {
    final term = search.trim();
    if (term.isEmpty || fields.isEmpty) return {'match_all': <String, dynamic>{}};
    return {
      'multi_match': {
        'query': term,
        'fields': fields,
        'type': 'best_fields',
        'operator': 'and',
      },
    };
  }

  void _replaceServerPage(String tableSlug, List<Map<String, dynamic>> rows) {
    final envelope = [
      for (final row in rows)
        {
          'table_slug': tableSlug,
          'row_id': row['src_hash']?.toString() ?? row['id']?.toString() ?? '${tableSlug}_${row.hashCode}',
          'body': Map<String, dynamic>.from(row),
        },
    ];
    _items.removeWhere((item) => item['table_slug'] == tableSlug);
    _items.addAll(envelope);
    notifyListeners();
  }

  Future<void> _loadServerPage(String tableSlug) async {
    final state = _serverPages.putIfAbsent(tableSlug, _ServerPageState.new);
    // equipment_search is now queried through the employee Search Index façade.
    // Keep the older virtual-table route as a compatibility fallback for
    // catalogs not yet represented by Search Index indexes.
    if (_useProjectInstance && _employeeSearchConfig(tableSlug) != null) {
      try {
        await _loadEmployeeSearchPage(tableSlug, state);
        return;
      } catch (_) {
        // Older deployments expose only the legacy module catalog endpoint.
      }
    }
    // equipment_search is an indexed catalog view shared from the cabinet
    // owner. The project runtime route exposes project rows, but does not
    // virtualize this OpenSearch table, so keep the employee caller on the
    // cabinet data endpoint even when the rest of the module uses a project
    // instance. This stays within the normal employee API scope and avoids
    // calling the pod/Bridge search route from Flutter.
    final useCabinetSearch = tableSlug == 'equipment_search';
    final res = _useProjectInstance && !useCabinetSearch
        ? await api.listProjectRuntimeModuleDataRowsPage(
            projectId: projectId!,
            moduleId: moduleId,
            tableSlug: tableSlug,
            sessionId: sessionId,
            page: state.page,
            pageSize: state.pageSize,
            search: state.search,
            inStockOnly: state.stockOnly,
          )
        : await api.listModuleDataRowsPage(
            cabinetId: cabinetId,
            moduleId: moduleId,
            tableSlug: tableSlug,
            sessionId: sessionId,
            page: state.page,
            pageSize: state.pageSize,
            search: state.search,
            inStockOnly: state.stockOnly,
          );
    state.total = res.total;
    final envelope = [
      for (final row in res.items)
        {
          'table_slug': tableSlug,
          // виртуальные строки не имеют row_id: ключ — src_hash позиции OS
          'row_id': row['row_id']?.toString() ??
              row['src_hash']?.toString() ??
              '${tableSlug}_${row.hashCode}',
          'body': Map<String, dynamic>.from(row),
        },
    ];
    _items.removeWhere((i) => i['table_slug'] == tableSlug);
    _items.addAll(envelope);
    notifyListeners();
  }

  Future<void> loadAll() async {
    final next = <Map<String, dynamic>>[];
    for (final table in _manifest.tables) {
      final slug = table['slug'] as String?;
      if (slug == null || slug.isEmpty) continue;
      if (isServerPaged(slug)) {
        // page 1 (or current pager state) loads below; skip full list
        continue;
      }
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
    final serverSlugs = [
      for (final table in _manifest.tables)
        if (isServerPaged(table['slug']?.toString() ?? ''))
          table['slug'].toString(),
    ];
    for (final slug in serverSlugs) {
      try {
        await _loadServerPage(slug);
      } catch (_) {
        // виртуальная таблица недоступна (OS down) — страница покажет empty
      }
    }
    final keepServer = _items
        .where((i) => serverSlugs.contains(i['table_slug']))
        .toList();
    if (_itemsFingerprint(next) ==
        _itemsFingerprint(_items.where((i) => !serverSlugs.contains(i['table_slug'])).toList())) {
      // Сессионные данные не изменились — но кросс-чатовые таблицы могли.
      await _reloadCrossChat();
      return;
    }
    _items
      ..clear()
      ..addAll(next)
      ..addAll(keepServer);
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
  Future<Map<String, dynamic>> invokeAction(
    String actionId, {
    String? rowId,
    Map<String, dynamic>? params,
  }) async {
    final result = await api.invokeModuleAction(
      cabinetId: cabinetId,
      moduleId: moduleId,
      actionId: actionId,
      rowId: rowId,
      params: params,
      projectId: projectId,
      sessionId: sessionId,
    );
    await loadAll();
    final movedSessionId = result['session_id']?.toString();
    final openedProjectId = result['project_id']?.toString();
    if (movedSessionId != null &&
        movedSessionId.isNotEmpty &&
        openedProjectId != null &&
        openedProjectId.isNotEmpty) {
      chatOpenIntent.value = ChatOpenIntent(
        projectId: openedProjectId,
        sessionId: movedSessionId,
        projectName: result['project_name']?.toString(),
        title: result['title']?.toString(),
      );
    }
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

class _ServerPageState {
  int page = 1;
  int pageSize = 50;
  int total = 0;
  String search = '';
  bool stockOnly = false;
}

