import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/features/meta/chat_header.dart';

Map<String, dynamic> _budgetListView() => {
      'slug': 'budget_lines_list',
      'table_slug': 'budget_lines',
      'kind': 'collection',
      'title': 'Бюджет',
      'ui_json': {
        'version': 1,
        'kind': 'collection',
        'chat_header': {
          'icon': 'request_quote',
          'label': {'ru': 'Бюджетирование', 'en': 'Budget'},
        },
      },
    };

void main() {
  group('collectChatHeaderButtons', () {
    test('collects views with ui_json.chat_header', () {
      final buttons = collectChatHeaderButtons([
        _budgetListView(),
        {
          'slug': 'plain_list',
          'kind': 'collection',
          'ui_json': {'version': 1, 'kind': 'collection'},
        },
      ]);

      expect(buttons, hasLength(1));
      expect(buttons.single.viewSlug, 'budget_lines_list');
      expect(buttons.single.icon, 'request_quote');
    });

    test('keeps raw label for locale resolution at render time', () {
      final buttons = collectChatHeaderButtons([_budgetListView()]);
      expect(buttons.single.label, isA<Map>());
      expect((buttons.single.label as Map)['ru'], 'Бюджетирование');
    });

    test('falls back to view title when chat_header has no label', () {
      final buttons = collectChatHeaderButtons([
        {
          'slug': 'budget_lines_list',
          'title': 'Бюджет',
          'ui_json': {
            'chat_header': {'icon': 'request_quote'},
          },
        },
      ]);
      expect(buttons.single.label, 'Бюджет');
    });

    test('skips views with empty slug or malformed ui_json', () {
      expect(
        collectChatHeaderButtons([
          {'slug': '', 'ui_json': {'chat_header': {'icon': 'x'}}},
          {'slug': 'no_ui'},
          {'slug': 'ui_not_map', 'ui_json': 'oops'},
          {
            'slug': 'header_not_map',
            'ui_json': {'chat_header': 'oops'},
          },
        ]),
        isEmpty,
      );
    });

    test('attaches module info when provided', () {
      final buttons = collectChatHeaderButtons(
        [_budgetListView()],
        moduleId: 'mod_equipment',
        moduleName: 'Подбор техники',
      );
      expect(buttons.single.moduleId, 'mod_equipment');
      expect(buttons.single.moduleName, 'Подбор техники');
    });
  });

  group('loadProjectChatHeaderButtons', () {
    test('merges module info and skips failing modules silently', () async {
      final buttons = await loadProjectChatHeaderButtons(
        modules: [
          {'module_id': 'mod_equipment', 'name': 'Подбор техники'},
          {'module_id': 'mod_broken', 'name': 'Broken'},
          {'module_id': '', 'name': 'No id'},
        ],
        fetchViews: (moduleId) async {
          if (moduleId == 'mod_broken') {
            throw StateError('meta unavailable');
          }
          return {'body': [_budgetListView()]};
        },
      );

      expect(buttons, hasLength(1));
      expect(buttons.single.moduleId, 'mod_equipment');
      expect(buttons.single.viewSlug, 'budget_lines_list');
    });

    test('accepts {body:{items:[…]}} documents and skips empty bodies',
        () async {
      final buttons = await loadProjectChatHeaderButtons(
        modules: [
          {'module_id': 'm1', 'name': 'One'},
          {'module_id': 'm2', 'name': 'Two'},
        ],
        fetchViews: (moduleId) async {
          if (moduleId == 'm1') return {'body': {'items': [_budgetListView()]}};
          return {'body': <dynamic>[]};
        },
      );
      expect(buttons, hasLength(1));
      expect(buttons.single.moduleId, 'm1');
    });

    test('yields no buttons when no module carries chat_header', () async {
      final buttons = await loadProjectChatHeaderButtons(
        modules: [
          {'module_id': 'm1', 'name': 'One'},
        ],
        fetchViews: (moduleId) async {
          return {
            'body': [
              {
                'slug': 'plain',
                'ui_json': {'kind': 'collection'},
              },
            ],
          };
        },
      );
      expect(buttons, isEmpty);
    });
  });
}
