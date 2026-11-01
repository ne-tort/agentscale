import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/features/meta/runtime/cabinet_data_controller.dart';

class _Api extends ProdavanApi {
  _Api() : super(baseUrl: 'http://test', bearerToken: 't');

  final List<String?> sessions = [];
  final List<String> pagedRoutes = [];
  final List<Map<String, dynamic>> searchRequests = [];
  bool failEmployeeSearch = false;
  String? invokeSession;
  String? createSession;

  @override
  Future<List<Map<String, dynamic>>> listModuleDataRows({
    required String cabinetId,
    required String moduleId,
    required String tableSlug,
    String? sessionId,
  }) async {
    sessions.add(sessionId);
    return const [];
  }

  @override
  Future<({List<Map<String, dynamic>> items, int total})> listModuleDataRowsPage({
    required String cabinetId,
    required String moduleId,
    required String tableSlug,
    String? sessionId,
    int page = 1,
    int pageSize = 50,
    String search = '',
    bool inStockOnly = false,
  }) async {
    pagedRoutes.add('cabinet:$tableSlug');
    return (items: <Map<String, dynamic>>[{'src_hash': 'hit-1'}], total: 1);
  }

  @override
  Future<({List<Map<String, dynamic>> items, int total})>
      listProjectRuntimeModuleDataRowsPage({
    required String projectId,
    required String moduleId,
    required String tableSlug,
    String? sessionId,
    int page = 1,
    int pageSize = 50,
    String search = '',
    bool inStockOnly = false,
  }) async {
    pagedRoutes.add('project:$tableSlug');
    return (items: <Map<String, dynamic>>[], total: 0);
  }

  @override
  Future<({List<Map<String, dynamic>> hits, int total, List<dynamic>? nextSearchAfter})>
      employeeSearchQuery({
    required String projectId,
    required String namespace,
    required String index,
    Map<String, dynamic>? query,
    Map<String, dynamic> filter = const {},
    List<Map<String, dynamic>>? sort,
    List<dynamic>? searchAfter,
    int from = 0,
    int size = 50,
  }) async {
    searchRequests.add({
      'projectId': projectId,
      'namespace': namespace,
      'index': index,
      'query': query,
      'filter': filter,
      'sort': sort,
      'searchAfter': searchAfter,
      'size': size,
    });
    if (failEmployeeSearch) throw const FormatException('unsupported response');
    return (
      hits: <Map<String, dynamic>>[{'src_hash': 'search-hit', 'title': 'Search hit'}],
      total: 1,
      nextSearchAfter: null,
    );
  }

  @override
  Future<Map<String, dynamic>> invokeModuleAction({
    required String cabinetId,
    required String moduleId,
    required String actionId,
    Map<String, dynamic>? params,
    String? rowId,
    String? projectId,
    String? sessionId,
  }) async {
    invokeSession = sessionId;
    return <String, dynamic>{'ok': true};
  }

  @override
  Future<Map<String, dynamic>> createModuleDataRow({
    required String cabinetId,
    required String moduleId,
    required String tableSlug,
    required Map<String, dynamic> body,
    String? sessionId,
  }) async {
    createSession = sessionId;
    return <String, dynamic>{'row_id': 'row_1', 'body': body};
  }
}

void main() {
  test('cabinet contour passes the active chat session to data calls', () async {
    final api = _Api();
    final manifest = ModuleMetaManifest.fromJson({
      'tables': [
        {'slug': 'request_lines'}
      ],
    });
    final controller = CabinetDataController(
      api: api,
      cabinetId: 'cab_1',
      moduleId: 'mod_equipment',
      manifest: manifest,
      sessionId: 'ags_123',
    );

    await controller.loadAll();
    expect(api.sessions, ['ags_123']);

    await controller.invokeAction('budget_sync_lines');
    expect(api.invokeSession, 'ags_123');

    await controller.createRow('request_lines', initial: {'title': 'SSD'});
    expect(api.createSession, 'ags_123');
  });

  test('project equipment search uses employee generic search then legacy fallback', () async {
    final api = _Api();
    final manifest = ModuleMetaManifest.fromJson({
      'tables': [
        {'slug': 'equipment_search'},
        {'slug': 'master_price'},
      ],
      'views': [
        {
          'slug': 'equipment_search_list',
          'table_slug': 'equipment_search',
          'ui_json': {
            'server_paged': true,
            'employee_search': {
              'namespace': 'equipment',
              'index': 'catalog_index',
              'search_fields': ['title', 'part_number'],
              'sort': [{'src_hash': 'asc'}],
              'stock_field': 'in_stock',
            },
          },
        },
        {
          'slug': 'master_price_list',
          'table_slug': 'master_price',
          'ui_json': {'server_paged': true},
        },
      ],
    });
    final controller = CabinetDataController(
      api: api,
      cabinetId: 'cab_1',
      projectId: 'project_1',
      moduleId: 'mod_equipment',
      manifest: manifest,
    );

    await controller.loadAll();

    expect(api.searchRequests, hasLength(1));
    expect(api.searchRequests.single['projectId'], 'project_1');
    expect(api.searchRequests.single['namespace'], 'equipment');
    expect(api.searchRequests.single['index'], 'catalog_index');
    expect(api.searchRequests.single['size'], 50);
    expect(api.searchRequests.single['sort'], [{'src_hash': 'asc'}]);
    expect(api.searchRequests.single['filter'], isEmpty);
    expect(
      (api.searchRequests.single['query'] as Map)['match_all'],
      isA<Map>(),
    );
    expect(api.pagedRoutes, ['project:master_price']);
    expect(controller.itemsForTable('equipment_search').single['row_id'], 'search-hit');

    api.failEmployeeSearch = true;
    await controller.reloadServerPage('equipment_search');
    expect(api.pagedRoutes, ['project:master_price', 'cabinet:equipment_search']);
  });
}
