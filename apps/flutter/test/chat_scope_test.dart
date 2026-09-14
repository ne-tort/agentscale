import 'package:flutter_test/flutter_test.dart';
import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/features/meta/module_meta_manifest.dart';
import 'package:prodavan/features/meta/runtime/cabinet_data_controller.dart';
import 'package:prodavan/features/meta/runtime/chat_scope.dart';

void main() {
  group('tableIsChatScoped', () {
    test('reads scope.chats current', () {
      expect(
        tableIsChatScoped({
          'slug': 'request_lines',
          'scope': {'chats': 'current'},
        }),
        isTrue,
      );
      expect(
        tableIsChatScoped({
          'slug': 'catalogs',
          'scope': {'chats': 'all'},
        }),
        isFalse,
      );
      expect(tableIsChatScoped({'slug': 'x'}), isFalse);
    });

    test('tableSlugIsChatScoped', () {
      final tables = [
        {
          'slug': 'catalogs',
          'scope': {'chats': 'all'},
        },
        {
          'slug': 'request_lines',
          'scope': {'chats': 'current'},
        },
      ];
      expect(tableSlugIsChatScoped(tables, 'request_lines'), isTrue);
      expect(tableSlugIsChatScoped(tables, 'catalogs'), isFalse);
    });
  });

  group('CabinetDataController.needsChatSession', () {
    test('true when chat-scoped table and no session', () {
      final ctrl = CabinetDataController(
        api: _FakeApi(),
        cabinetId: 'cab_1',
        moduleId: 'mod_1',
        manifest: ModuleMetaManifest(
          tables: [
            {
              'slug': 'request_lines',
              'scope': {'chats': 'current'},
            },
          ],
        ),
      );
      expect(ctrl.needsChatSession, isTrue);
    });

    test('false when session present', () {
      final ctrl = CabinetDataController(
        api: _FakeApi(),
        cabinetId: 'cab_1',
        moduleId: 'mod_1',
        sessionId: 'ags_1',
        manifest: ModuleMetaManifest(
          tables: [
            {
              'slug': 'request_lines',
              'scope': {'chats': 'current'},
            },
          ],
        ),
      );
      expect(ctrl.needsChatSession, isFalse);
    });

    test('false when only shared tables', () {
      final ctrl = CabinetDataController(
        api: _FakeApi(),
        cabinetId: 'cab_1',
        moduleId: 'mod_1',
        manifest: ModuleMetaManifest(
          tables: [
            {
              'slug': 'catalogs',
              'scope': {'chats': 'all'},
            },
          ],
        ),
      );
      expect(ctrl.needsChatSession, isFalse);
    });
  });
}

class _FakeApi extends Fake implements ProdavanApi {}
