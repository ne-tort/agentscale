import 'package:flutter_test/flutter_test.dart';
import 'package:prodavan/features/meta/widgets/project_multiselect_field.dart';

void main() {
  test('resolveProjectIdsPickerToggle keeps All exclusive', () {
    expect(
      resolveProjectIdsPickerToggle({kProjectIdsAllSentinel}, 'p1', true),
      {'p1'},
    );
    expect(
      resolveProjectIdsPickerToggle({'p1'}, 'p2', true),
      {'p1', 'p2'},
    );
    expect(
      resolveProjectIdsPickerToggle({'p1'}, 'p1', false),
      {kProjectIdsAllSentinel},
    );
    expect(
      resolveProjectIdsPickerToggle({'p1'}, kProjectIdsAllSentinel, true),
      {kProjectIdsAllSentinel},
    );
  });

  test('projectIdsStoredFromPicker maps All to empty list', () {
    expect(
      projectIdsStoredFromPicker(
        {kProjectIdsAllSentinel},
        allowedProjectIds: {'p1', 'p2'},
      ),
      <String>{},
    );
    expect(
      projectIdsStoredFromPicker(
        {'p1', 'ghost'},
        allowedProjectIds: {'p1', 'p2'},
      ),
      {'p1'},
    );
  });
}
