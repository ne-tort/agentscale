import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/session/work_context.dart';

void main() {
  tearDown(() {
    workContext.clear();
  });

  test('notifyProjectLifecycleChanged bumps epoch and notifies', () {
    var ticks = 0;
    void listener() => ticks++;
    workContext.addListener(listener);
    addTearDown(() => workContext.removeListener(listener));

    expect(workContext.projectLifecycleEpoch, 0);
    workContext.notifyProjectLifecycleChanged();
    expect(workContext.projectLifecycleEpoch, 1);
    expect(ticks, 1);
    workContext.notifyProjectLifecycleChanged();
    expect(workContext.projectLifecycleEpoch, 2);
    expect(ticks, 2);
  });

  test('clear resets lifecycle epoch', () {
    workContext.notifyProjectLifecycleChanged();
    workContext.clear();
    expect(workContext.projectLifecycleEpoch, 0);
  });

  test('selectedSessionId persists until project/cabinet change', () {
    workContext.setSelectedSessionId('ags_1');
    expect(workContext.selectedSessionId, 'ags_1');
    workContext.setSelectedSessionId('ags_1');
    expect(workContext.selectedSessionId, 'ags_1');

    workContext.setSelectedProjectId('proj_a');
    expect(workContext.selectedSessionId, isNull);

    workContext.setSelectedSessionId('ags_2');
    workContext.enterCabinet('cab_1');
    expect(workContext.selectedSessionId, isNull);
  });

  test('setSelectedSessionId trims empty to null', () {
    workContext.setSelectedSessionId('  ');
    expect(workContext.selectedSessionId, isNull);
    workContext.setSelectedSessionId(' ags_x ');
    expect(workContext.selectedSessionId, 'ags_x');
  });
}
