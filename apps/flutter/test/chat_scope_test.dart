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

  group('scope.active_chat', () {
    test('required hides without session', () {
      final item = {
        'title': 'Позиции',
        'scope': {'active_chat': 'required'},
      };
      expect(scopeRequiresActiveChat(item), isTrue);
      expect(scopeVisibleForSession(item, null), isFalse);
      expect(scopeVisibleForSession(item, ''), isFalse);
      expect(scopeVisibleForSession(item, 'ags_1'), isTrue);
    });

    test('optional always visible', () {
      final item = {
        'title': 'Каталоги',
        'scope': {'active_chat': 'optional'},
      };
      expect(scopeVisibleForSession(item, null), isTrue);
      expect(scopeVisibleForSession({'title': 'x'}, null), isTrue);
    });

    test('effectiveChatSessionId falls back to main', () {
      expect(effectiveChatSessionId(null), kDefaultChatSessionId);
      expect(effectiveChatSessionId(''), kDefaultChatSessionId);
      expect(effectiveChatSessionId('ags_1'), 'ags_1');
    });
  });

  group('CabinetDataController', () {
    test('loads without requiring chat session flag', () {
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
      expect(ctrl.sessionId, isNull);
    });
  });
}

class _FakeApi extends Fake implements ProdavanApi {}
