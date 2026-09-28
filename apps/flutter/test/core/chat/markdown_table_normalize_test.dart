import 'package:flutter_test/flutter_test.dart';
import 'package:prodavan/core/chat/markdown_table_normalize.dart';

void main() {
  group('normalizeChatMarkdownTables', () {
    test('leaves valid GFM table unchanged', () {
      const src = '''
| A | B |
|---|---|
| 1 | 2 |
''';
      expect(normalizeChatMarkdownTables(src.trim()), src.trim());
    });

    test('expands double-pipe glued rows with separator', () {
      const src =
          '|| Категория | Статус | Детали || |-------|-------|-------| || **Shell** | ✅ | Linux WSL2 ||';
      final out = normalizeChatMarkdownTables(src);
      final lines = out.split('\n');
      expect(lines.length, greaterThanOrEqualTo(3));
      expect(lines[0], contains('Категория'));
      expect(lines[0].startsWith('|'), isTrue);
      expect(lines[0].contains('||'), isFalse);
      expect(lines.any(_isSep), isTrue);
      expect(out, contains('Shell'));
      expect(out, isNot(contains('||')));
    });

    test('inserts separator when header+body lack one', () {
      const src = '''
| Name | Value |
| foo | bar |
''';
      final out = normalizeChatMarkdownTables(src.trim());
      final lines = out.split('\n');
      expect(lines.length, 3);
      expect(_isSep(lines[1]), isTrue);
    });

    test('multi-row GFM body stays without extra separators', () {
      const src = '''
| A | B |
|---|---|
| 1 | 2 |
| 3 | 4 |
''';
      expect(normalizeChatMarkdownTables(src.trim()), src.trim());
    });

    test('does not alter prose with a single pipe', () {
      const src = 'Use A | B as alternatives.';
      expect(normalizeChatMarkdownTables(src), src);
    });

    test('glued single-pipe rows break on empty cell boundary', () {
      const src = '| A | B | |---|---| | 1 | 2 |';
      final out = normalizeChatMarkdownTables(src);
      expect(out.split('\n').length, greaterThanOrEqualTo(3));
      expect(out, contains('A'));
      expect(out, contains('1'));
    });

    test('does not touch lines inside fenced code blocks', () {
      const src = '''
|| a | b || |---|---| || 1 | 2 ||

```dart
|| glued | table ||
| not | a | table |
```

~~~text
| tilde | fence |
~~~

| A | B |
|---|---|
| x | y |
''';
      final out = normalizeChatMarkdownTables(src);
      final lines = out.split('\n');
      // Outside the fences: the glued row is expanded as before.
      expect(lines.first, isNot(contains('||')));
      expect(lines.first, contains('a'));
      // Fence markers preserved.
      expect(out, contains('```dart'));
      expect(out, contains('~~~text'));
      // Inside the fences: content untouched.
      expect(out, contains('|| glued | table ||'));
      expect(out, contains('| not | a | table |'));
      expect(out, contains('| tilde | fence |'));
      // Table after the fences keeps its own separator (no double insertion).
      expect(out, contains('| A | B |'));
      expect(out, isNot(contains('|---|---|---|')));
    });
  });
}

bool _isSep(String line) {
  final t = line.trim();
  return RegExp(r'^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$').hasMatch(t);
}
