import 'dart:async';

import 'package:flutter_test/flutter_test.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/jobs/app_job_store.dart';
import 'package:prodavan/core/jobs/project_lifecycle_jobs.dart';
import 'package:prodavan/l10n/app_localizations_ru.dart';

class _FakeApi extends Fake implements ProdavanApi {
  int launchCalls = 0;
  Object? launchError;
  Map<String, dynamic> container = const {
    'observed_state': 'running',
    'runtime': {'observed_state': 'running'},
  };

  @override
  Future<Map<String, dynamic>> launchProject(String projectId) async {
    launchCalls += 1;
    final err = launchError;
    if (err != null) throw err;
    return {'id': projectId, 'status': 'active'};
  }

  @override
  Future<Map<String, dynamic>> getProjectContainer(String projectId) async {
    return Map<String, dynamic>.from(container);
  }
}

void main() {
  test('AppJobStore start updates label and finishes', () async {
    final store = AppJobStore();
    final job = await store.start(
      kind: AppJobKinds.projectLaunch,
      subjectId: 'prj_1',
      label: 'start',
      run: (ctrl) async {
        ctrl.setLabel('pulling');
      },
    );
    expect(job.status, AppJobStatus.succeeded);
    expect(
      store.bySubject(kind: AppJobKinds.projectLaunch, subjectId: 'prj_1')!.label,
      'pulling',
    );
  });

  test('runProjectLaunchJob treats POD_ALREADY_EXISTS as join', () async {
    final store = AppJobStore();
    final api = _FakeApi()
      ..launchError = ProdavanApiException(
        409,
        '{"code":"POD_ALREADY_EXISTS","detail":"project pod already exists"}',
      );
    final l10n = AppLocalizationsRu();

    final container = await runProjectLaunchJob(
      store: store,
      api: api,
      projectId: 'prj_join',
      l10n: l10n,
    );

    expect(api.launchCalls, 1);
    expect(container?['observed_state'], 'running');
    expect(
      store.bySubject(kind: AppJobKinds.projectLaunch, subjectId: 'prj_join')!.status,
      AppJobStatus.succeeded,
    );
  });

  test('second start while running returns same job', () async {
    final store = AppJobStore();
    final gate = Completer<void>();
    final first = store.start(
      kind: AppJobKinds.projectLaunch,
      subjectId: 'prj_2',
      label: 'a',
      run: (_) => gate.future,
    );
    await Future<void>.delayed(Duration.zero);
    expect(store.isActive(kind: AppJobKinds.projectLaunch, subjectId: 'prj_2'), isTrue);

    var ranSecond = false;
    final second = await store.start(
      kind: AppJobKinds.projectLaunch,
      subjectId: 'prj_2',
      label: 'b',
      run: (_) async {
        ranSecond = true;
      },
    );
    expect(second.status, AppJobStatus.running);
    expect(ranSecond, isFalse);

    gate.complete();
    await first;
    expect(
      store.bySubject(kind: AppJobKinds.projectLaunch, subjectId: 'prj_2')!.status,
      AppJobStatus.succeeded,
    );
  });
}
