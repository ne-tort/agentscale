import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:prodavan/core/chat/tool_activity_labels.dart';
import 'package:prodavan/l10n/app_localizations.dart';

void main() {
  group('normalizeToolKind', () {
    test('maps SDK aliases', () {
      expect(normalizeToolKind('Read'), ToolKind.fileRead);
      expect(normalizeToolKind('delete'), ToolKind.fileDelete);
      expect(normalizeToolKind('Glob'), ToolKind.searchGlob);
      expect(normalizeToolKind('grep'), ToolKind.searchGrep);
      expect(normalizeToolKind('ls'), ToolKind.listDir);
      expect(normalizeToolKind('mcp__server__tool'), ToolKind.mcp);
      expect(normalizeToolKind('task'), ToolKind.subagent);
      expect(normalizeToolKind('unknown_tool'), ToolKind.generic);
    });
  });

  group('extractToolContext', () {
    test('extracts path from input keys', () {
      expect(
        extractToolContextPath({'target_file': 'lib/a.dart'}),
        'lib/a.dart',
      );
      expect(
        extractToolContextPattern({'globPattern': '**/*.dart'}),
        '**/*.dart',
      );
      expect(
        extractToolContextCommand({'command': 'flutter test'}),
        'flutter test',
      );
    });
  });

  group('unwrapToolPayload', () {
    test('unwraps status/value envelope', () {
      final unwrapped = unwrapToolPayload({
        'status': 'success',
        'value': {'fileSize': 304},
      });
      expect(unwrapped, isA<Map>());
      expect((unwrapped as Map)['fileSize'], 304);
    });

    test('unwraps success envelope', () {
      final unwrapped = unwrapToolPayload({
        'success': {'content': 'hello', 'path': 'a.txt'},
      });
      expect(unwrapped, isA<Map>());
      expect((unwrapped as Map)['content'], 'hello');
    });
  });

  group('formatToolPanelBody', () {
    test('delete shows path and fileSize, not raw JSON', () {
      final body = formatToolPanelBody(
        kind: ToolKind.fileDelete,
        input: {'path': 'old.txt'},
        output: {
          'status': 'success',
          'value': {'fileSize': 304},
        },
      );
      expect(body, 'old.txt\nfileSize: 304');
      expect(body.contains('{'), isFalse);
    });

    test('write shows path and diff stats', () {
      final body = formatToolPanelBody(
        kind: ToolKind.fileWrite,
        input: {'path': 'a.py'},
        output: {
          'status': 'success',
          'value': {'lines_added': 3, 'lines_removed': 1},
        },
      );
      expect(body, contains('a.py'));
      expect(body, contains('+3 −1'));
      expect(body.trim().isNotEmpty, isTrue);
    });

    test('edit with empty output still shows path', () {
      final body = formatToolPanelBody(
        kind: ToolKind.fileEdit,
        input: {'path': 'b.py'},
        output: null,
      );
      expect(body, 'b.py');
    });

    test('generic falls back to pretty JSON when body empty', () {
      final body = formatToolPanelBody(
        kind: ToolKind.generic,
        input: const {},
        output: {'status': 'success', 'value': {'ok': true}},
      );
      expect(body.contains('ok'), isTrue);
      expect(body.trim().isNotEmpty, isTrue);
    });

    test('glob lists files from value', () {
      final body = formatToolPanelBody(
        kind: ToolKind.searchGlob,
        input: {'globPattern': '*.dart'},
        output: {
          'status': 'success',
          'value': {
            'files': ['a.dart', 'b.dart'],
          },
        },
      );
      expect(body, 'a.dart\nb.dart');
    });

    test('read returns content', () {
      final body = formatToolPanelBody(
        kind: ToolKind.fileRead,
        input: {'path': 'a.txt'},
        output: {
          'success': {'content': 'line1\nline2'},
        },
      );
      expect(body, 'line1\nline2');
    });
  });

  group('formatToolActivityLabel', () {
    late AppLocalizations l10n;

    setUpAll(() async {
      l10n = await AppLocalizations.delegate.load(const Locale('ru'));
    });

    test('delete with path', () {
      final result = formatToolActivityLabel(
        l10n,
        name: 'delete',
        input: {'path': 'old.txt'},
      );
      expect(result.label, 'Удалён old.txt');
    });

    test('delete label uses path even when output has no path', () {
      final result = formatToolActivityLabel(
        l10n,
        name: 'delete',
        input: {'path': 'gone.txt'},
        output: {
          'status': 'success',
          'value': {'fileSize': 10},
        },
      );
      expect(result.label, 'Удалён gone.txt');
      expect(result.detail, isNull);
    });

    test('glob with pattern', () {
      final result = formatToolActivityLabel(
        l10n,
        name: 'glob',
        input: {'glob_pattern': '*.dart'},
      );
      expect(result.label, 'Поиск файлов *.dart');
    });

    test('pending suffix', () {
      final result = formatToolActivityLabel(
        l10n,
        name: 'grep',
        input: {'pattern': 'foo'},
        pending: true,
      );
      expect(result.label, endsWith('…'));
    });

    test('built-in utilities get human labels for bare / mcp / underscored names', () {
      for (final name in [
        'todo.write',
        'mcp.openclaw.todo.write',
        'todo_write',
        'mcp_openclaw_todo_write',
      ]) {
        expect(
          formatToolActivityLabel(l10n, name: name).label,
          'Обновление плана задач',
          reason: name,
        );
      }
      for (final name in ['todo.list', 'mcp.openclaw.todo.list', 'todo_list']) {
        expect(formatToolActivityLabel(l10n, name: name).label, 'План задач', reason: name);
      }
    });

    test('unknown module tool shows a readable server · tool label, not "Инструмент"', () {
      // A namespaced MCP tool with no alias reads as "server · tool"; the
      // generic "Инструмент …" prefix is reserved for truly unknown bare names.
      final namespaced = formatToolActivityLabel(l10n, name: 'mcp.vendor.mystery_tool');
      expect(namespaced.label, 'vendor · mystery_tool');
      expect(namespaced.label, isNot(startsWith('Инструмент')));

      final bare = formatToolActivityLabel(l10n, name: 'mystery_tool');
      expect(bare.label, startsWith('Инструмент'));
    });
  });

  group('parseDiffStats', () {
    test('gated: grep-like output without diff markers → null', () {
      // Grep hits and notes may start lines with '+'/'-' but are not diffs.
      expect(parseDiffStats('src/a.dart:42: +foo\nsrc/b.dart:1: -bar'), isNull);
      expect(parseDiffStats('+hello\n-world'), isNull);
      expect(parseDiffStats('random notes\n+1\n-2'), isNull);
    });

    test('unified diff with hunk marker counts +/- lines', () {
      final stats = parseDiffStats(
        '--- a/f.py\n+++ b/f.py\n@@ -1,2 +1,3 @@\n+added\n-removed\n context',
      );
      expect(stats?.added, 1);
      expect(stats?.removed, 1);
    });

    test('diff --git header is enough to gate in', () {
      final stats = parseDiffStats('diff --git a/x b/x\n+new line');
      expect(stats?.added, 1);
      expect(stats?.removed, 0);
    });

    test('structural map payloads bypass the gate', () {
      final stats = parseDiffStats({'lines_added': 2, 'lines_removed': 1});
      expect(stats?.added, 2);
      expect(stats?.removed, 1);
    });
  });
}
