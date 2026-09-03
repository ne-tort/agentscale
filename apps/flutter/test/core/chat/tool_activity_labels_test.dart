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
    test('delete shows fileSize only, not raw JSON', () {
      final body = formatToolPanelBody(
        kind: ToolKind.fileDelete,
        input: {'path': 'old.txt'},
        output: {
          'status': 'success',
          'value': {'fileSize': 304},
        },
      );
      expect(body, 'fileSize: 304');
      expect(body.contains('{'), isFalse);
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
  });
}
