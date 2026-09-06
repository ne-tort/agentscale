import 'package:flutter_test/flutter_test.dart';
import 'package:prodavan/features/meta/widgets/column_map_field.dart';

void main() {
  test('parseColumnsJson accepts list and json string', () {
    expect(parseColumnsJson(['a', 'b']), ['a', 'b']);
    expect(parseColumnsJson('["title","price"]'), ['title', 'price']);
    expect(parseColumnsJson(null), isEmpty);
    expect(parseColumnsJson(''), isEmpty);
  });
}
