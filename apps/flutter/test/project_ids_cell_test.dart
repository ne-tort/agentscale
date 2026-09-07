import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/features/meta/project_ids_cell.dart';

void main() {
  test('formatProjectIdsCell empty means all', () {
    expect(formatProjectIdsCell(null), 'Все');
    expect(formatProjectIdsCell(<dynamic>[]), 'Все');
    expect(formatProjectIdsCell(null, english: true), 'All');
  });

  test('formatProjectIdsCell counts ids', () {
    expect(formatProjectIdsCell(['a', 'b']), '2');
    expect(formatProjectIdsCell(['only']), '1');
  });
}
