import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/features/meta/module_cell_format.dart';

void main() {
  group('money format', () {
    test('groups thousands, rub suffix', () {
      expect(formatMoneyRub(1234567.8), '1 234 567,80 ₽');
      expect(formatMoneyRub(0), '0,00 ₽');
      expect(formatMoneyRub(-42.5), '-42,50 ₽');
    });

    test('format: money via formatModuleCell', () {
      final out = formatModuleCell(
        item: {'row_id': 't1'},
        body: {'sum_rub': 28921.12},
        col: {'field': 'sum_rub', 'format': 'money'},
        tableSlug: 'tenders',
      );
      expect(out, '28 921,12 ₽');
    });

    test('money null → empty', () {
      final out = formatModuleCell(
        item: {'row_id': 't1'},
        body: {'sum_rub': null},
        col: {'field': 'sum_rub', 'format': 'money'},
        tableSlug: 'tenders',
      );
      expect(out, '');
    });
  });

  group('percent format', () {
    test('one decimal, trailing zero trimmed', () {
      String cell(dynamic v) => formatModuleCell(
            item: {'row_id': 't1'},
            body: {'found_pct': v},
            col: {'field': 'found_pct', 'format': 'percent'},
            tableSlug: 'tenders',
          );
      expect(cell(50.0), '50%');
      expect(cell(33.3), '33,3%');
      expect(cell(100), '100%');
      expect(cell(null), '');
    });
  });

  group('deadline format', () {
    final now = DateTime(2026, 10, 17, 12, 0);

    test('days + hours', () {
      final d = now.add(const Duration(days: 2, hours: 12));
      expect(formatDeadlineLabel(d.toIso8601String(), now: now), '2 д. 12 ч.');
    });

    test('less than a day → hours + minutes', () {
      final d = now.add(const Duration(hours: 5, minutes: 30));
      expect(formatDeadlineLabel(d.toIso8601String(), now: now), '5 ч. 30 м.');
    });

    test('less than an hour → minutes', () {
      final d = now.add(const Duration(minutes: 45));
      expect(formatDeadlineLabel(d.toIso8601String(), now: now), '45 м.');
    });

    test('expired → Истёк', () {
      final d = now.subtract(const Duration(hours: 1));
      expect(formatDeadlineLabel(d.toIso8601String(), now: now), 'Истёк');
    });

    test('empty/null → dash', () {
      expect(formatDeadlineLabel(null, now: now), '—');
      expect(formatDeadlineLabel('', now: now), '—');
    });

    test('state classification', () {
      expect(
        deadlineStateOf(now.add(const Duration(days: 3)).toIso8601String(), now: now),
        DeadlineState.ok,
      );
      expect(
        deadlineStateOf(now.add(const Duration(hours: 3)).toIso8601String(), now: now),
        DeadlineState.soon,
      );
      expect(
        deadlineStateOf(now.subtract(const Duration(minutes: 1)).toIso8601String(), now: now),
        DeadlineState.expired,
      );
      expect(deadlineStateOf(null, now: now), DeadlineState.none);
    });

    test('format: deadline via formatModuleCell', () {
      final d = DateTime.now().add(const Duration(days: 1, hours: 2));
      final out = formatModuleCell(
        item: {'row_id': 't1'},
        body: {'deadline_at': d.toIso8601String()},
        col: {'field': 'deadline_at', 'format': 'deadline'},
        tableSlug: 'tenders',
      );
      expect(out, startsWith('1 д.'));
    });
  });

  group('lookup_table (tender_id → tenders.title)', () {
    test('resolves title from related table', () {
      final out = formatModuleCell(
        item: {'row_id': 'l1'},
        body: {'tender_id': 't1'},
        col: {'field': 'tender_id', 'lookup_table': 'tenders'},
        tableSlug: 'request_lines',
        itemsForTable: (slug) => slug == 'tenders'
            ? [
                {'row_id': 't1', 'body': {'title': 'Поставка для Рога'}},
              ]
            : const [],
      );
      expect(out, 'Поставка для Рога');
    });

    test('unknown id / empty value → empty', () {
      String cell(dynamic v) => formatModuleCell(
            item: {'row_id': 'l1'},
            body: {'tender_id': v},
            col: {'field': 'tender_id', 'lookup_table': 'tenders'},
            tableSlug: 'request_lines',
            itemsForTable: (slug) => const [],
          );
      expect(cell('missing'), '');
      expect(cell(''), '');
      expect(cell(null), '');
    });
  });
}
