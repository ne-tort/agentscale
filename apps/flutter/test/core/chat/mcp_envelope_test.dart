import 'package:flutter_test/flutter_test.dart';
import 'package:prodavan/core/chat/tool_activity_labels.dart';

/// MCP result envelope unwrapping: `{content: [{text: …}]}` → payload (with
/// the nested JSON parsed and the module-row `body` preferred), plus the
/// isError detection for red labels and the Запрос/Ответ error panel.
void main() {
  group('mcpToolFailed', () {
    test('MCP isError envelope is a failure', () {
      expect(
        mcpToolFailed({
          'content': [
            {'text': 'title is required when creating request_lines', 'type': 'text'},
          ],
          'isError': true,
        }),
        isTrue,
      );
    });

    test('plain envelopes are not failures', () {
      expect(mcpToolFailed(null), isFalse);
      expect(mcpToolFailed({'content': []}), isFalse);
      expect(mcpToolFailed('string'), isFalse);
    });
  });

  group('unwrapToolPayload MCP envelope', () {
    test('success: content text JSON with body → body only', () {
      final output = {
        'content': [
          {
            'type': 'text',
            'text':
                '{"module_id":"mod_equipment","row_id":"row_e128a92bfbcd","table_slug":"found_groups",'
                '"body":{"note":"PP6-2M Cablexpert","is_best":true,"face_price":214.55},'
                '"session_id":"ags_1","created_at":"2026-10-02T23:16:28Z"}',
          }
        ]
      };
      final unwrapped = unwrapToolPayload(output);
      expect(unwrapped, isA<Map>());
      final map = unwrapped as Map;
      expect(map['is_best'], isTrue);
      expect(map['face_price'], 214.55);
      expect(map.containsKey('module_id'), isFalse);
      expect(map.containsKey('session_id'), isFalse);
    });

    test('success: content text JSON without body → parsed document', () {
      final output = {
        'content': [
          {'type': 'text', 'text': '{"rows": 3, "ok": true}'},
        ]
      };
      final unwrapped = unwrapToolPayload(output);
      expect((unwrapped as Map)['rows'], 3);
    });

    test('content text is plain string (not JSON) → string as-is', () {
      final output = {
        'content': [
          {'type': 'text', 'text': 'ok'},
        ]
      };
      expect(unwrapToolPayload(output), 'ok');
    });

    test('non-MCP maps unchanged', () {
      expect(unwrapToolPayload({'value': 5}), 5);
      expect(unwrapToolPayload({'fileSize': 3}), isA<Map>());
    });
  });

  group('mcpToolErrorText', () {
    test('extracts the error answer text', () {
      final output = {
        'content': [
          {'text': 'title is required when creating request_lines', 'type': 'text'},
        ],
        'isError': true,
      };
      expect(
        mcpToolErrorText(output),
        'title is required when creating request_lines',
      );
    });
  });

  test('formatToolPanelBody: MCP error → Запрос + Ответ', () {
    final body = formatToolPanelBody(
      kind: ToolKind.mcp,
      input: {'table_slug': 'request_lines', 'body': {'qty': 2}},
      output: {
        'content': [
          {'text': 'title is required when creating request_lines', 'type': 'text'},
        ],
        'isError': true,
      },
    );
    expect(body, contains('Запрос:'));
    expect(body, contains('Ответ:'));
    expect(body, contains('title is required'));
    expect(body, contains('"table_slug"'));
  });

  test('formatToolPanelBody: MCP success → pretty payload', () {
    final body = formatToolPanelBody(
      kind: ToolKind.mcp,
      output: {
        'content': [
          {'type': 'text', 'text': '{"ok": true}'},
        ]
      },
    );
    expect(body, contains('"ok"'));
    expect(body.contains('content'), isFalse);
  });
}
