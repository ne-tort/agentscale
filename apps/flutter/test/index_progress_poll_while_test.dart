import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/features/meta/module_cell_format.dart';
import 'package:prodavan/features/meta/poll_while.dart';

void main() {
  group('formatIndexProgressCell (format: index_progress)', () {
    String cell(Map<String, dynamic> body, Map<String, dynamic> col) {
      return formatModuleCell(
        item: {'row_id': 'r1'},
        body: body,
        col: col,
        tableSlug: 'catalogs',
      );
    }

    final col = {
      'field': 'status',
      'format': 'index_progress',
      'enum': {
        'values': ['draft', 'queued', 'indexing', 'ready', 'error'],
        'labels': {
          'draft': 'Без индексирования',
          'queued': 'В очереди',
          'indexing': 'В процессе',
          'ready': 'Обработано',
          'error': 'Ошибка',
        },
      },
    };

    test('indexing with indexed_count and total_rows renders «x из y»', () {
      final text = cell({
        'status': 'indexing',
        'indexed_count': 42,
        'total_rows': 100,
      }, col);
      expect(text, 'В процессе (42 из 100)');
    });

    test('indexing with indexed_count only renders «x»', () {
      final text = cell({'status': 'indexing', 'indexed_count': 7}, col);
      expect(text, 'В процессе (7)');
    });

    test('indexing without heartbeat renders plain label', () {
      final text = cell({'status': 'indexing'}, col);
      expect(text, 'В процессе');
    });

    test('non-indexing statuses fall through to enum labels', () {
      expect(cell({'status': 'ready'}, col), 'Обработано');
      expect(cell({'status': 'error'}, col), 'Ошибка');
      expect(cell({'status': 'queued'}, col), 'В очереди');
    });

    test('stringified numbers are parsed', () {
      final text = cell({
        'status': 'indexing',
        'indexed_count': '3',
        'total_rows': '9',
      }, col);
      expect(text, 'В процессе (3 из 9)');
    });
  });

  group('parsePollWhile', () {
    test('parses field/equals/interval', () {
      final config = parsePollWhile({
        'field': 'status',
        'equals': 'indexing',
        'interval_ms': 3000,
      });
      expect(config, isNotNull);
      expect(config!.field, 'status');
      expect(config.matches('indexing'), isTrue);
      expect(config.matches('ready'), isFalse);
      expect(config.interval, const Duration(milliseconds: 3000));
    });

    test('null when absent or malformed', () {
      expect(parsePollWhile(null), isNull);
      expect(parsePollWhile('x'), isNull);
      expect(parsePollWhile({'field': 'status'}), isNull);
      expect(
        parsePollWhile({'field': 'status', 'equals': 'x', 'interval_ms': 0}),
        isNull,
      );
    });

    test('list equals matches any value (queued/indexing)', () {
      final config = parsePollWhile({
        'field': 'status',
        'equals': ['queued', 'indexing'],
        'interval_ms': 3000,
      });
      expect(config!.matches('queued'), isTrue);
      expect(config.matches('indexing'), isTrue);
      expect(config.matches('ready'), isFalse);
      expect(config.matches('error'), isFalse);
    });

    test('bool equals matches truthiness', () {
      final config = parsePollWhile({
        'field': 'busy',
        'equals': true,
        'interval_ms': 1000,
      });
      expect(config!.matches(true), isTrue);
      expect(config.matches(false), isFalse);
    });
  });
}
