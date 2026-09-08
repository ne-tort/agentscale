import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/features/meta/project_ids_cell.dart';
import 'package:prodavan/features/meta/workspace_path.dart';

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

  test('normalizeWorkspaceRelativePath strips roots and backslashes', () {
    expect(normalizeWorkspaceRelativePath(r'\skills\'), 'skills');
    expect(normalizeWorkspaceRelativePath('/skills/'), 'skills');
    expect(normalizeWorkspaceRelativePath(''), '');
    expect(normalizeWorkspaceRelativePath('../x'), '');
  });

  test('formatPathCell empty is em dash', () {
    expect(formatPathCell(null), '—');
    expect(formatPathCell(''), '—');
    expect(formatPathCell('  '), '—');
    expect(formatPathCell('/rules/'), 'rules');
  });
}
