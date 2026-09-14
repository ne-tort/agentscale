import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/features/employee/project_modules_table.dart';

void main() {
  test('projectModuleBindKind defaults enabled row to global', () {
    expect(
      projectModuleBindKind({'module_id': 'mod_equipment', 'enabled': true}),
      'global',
    );
    expect(
      projectModuleBindKind({
        'module_id': 'mod_equipment',
        'enabled': true,
        'bind_kind': 'local',
      }),
      'local',
    );
    expect(
      projectModuleBindKind({'module_id': 'mod_x', 'enabled': false}),
      '',
    );
  });
}
