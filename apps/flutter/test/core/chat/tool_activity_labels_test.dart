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
