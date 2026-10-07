import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/features/meta/runtime/cabinet_data_controller.dart';

class _Api extends ProdavanApi {
  _Api() : super(baseUrl: 'http://test', bearerToken: 't');

  final List<String?> sessions = [];
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
}
