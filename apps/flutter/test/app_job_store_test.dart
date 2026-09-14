import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

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
  setUp(() {
    SharedPreferences.setMockInitialValues({});
  });

  test('AppJobStore start keeps title and updates subtitle', () async {
    final store = AppJobStore();
    await store.ensureHydrated();
    final job = await store.start(
      kind: AppJobKinds.projectLaunch,
      subjectId: 'prj_1',
      title: 'Запуск проекта…',
      subtitle: 'start',
      run: (ctrl) async {
        ctrl.setSubtitle('pulling');
      },
    );
    expect(job.status, AppJobStatus.succeeded);
    expect(job.title, 'Запуск проекта…');
    expect(
      store.bySubject(kind: AppJobKinds.projectLaunch, subjectId: 'prj_1')!.subtitle,
      'pulling',
    );
  });

  test('second start while running awaits same job', () async {
    final store = AppJobStore();
    await store.ensureHydrated();
    final gate = Completer<void>();
    final first = store.start(
      kind: AppJobKinds.projectLaunch,
      subjectId: 'prj_2',
      title: 'a',
      run: (_) => gate.future,
    );
    await Future<void>.delayed(Duration.zero);
    expect(store.isActive(kind: AppJobKinds.projectLaunch, subjectId: 'prj_2'), isTrue);

    var ranSecond = false;
    final secondFuture = store.start(
      kind: AppJobKinds.projectLaunch,
      subjectId: 'prj_2',
      title: 'b',
      run: (_) async {
        ranSecond = true;
      },
    );

    gate.complete();
    final firstJob = await first;
    final secondJob = await secondFuture;
    expect(firstJob.status, AppJobStatus.succeeded);
    expect(secondJob.status, AppJobStatus.succeeded);
    expect(ranSecond, isFalse);
  });

  test('expired job finishes as failed', () async {
    final store = AppJobStore();
    await store.ensureHydrated();
    final job = await store.start(
      kind: AppJobKinds.projectReload,
      subjectId: 'prj_to',
      title: 'Перезагрузка проекта…',
      timeout: const Duration(milliseconds: 1),
      run: (_) async {
        await Future<void>.delayed(const Duration(milliseconds: 50));
      },
    );
    expect(job.status, AppJobStatus.failed);
    expect(job.error, isNotNull);
  });

  test('persisted running job restores after hydrate', () async {
    SharedPreferences.setMockInitialValues({});
    final prefs = await SharedPreferences.getInstance();
    final started = DateTime.now();
    final persisted = AppJob(
      id: 'project.reload_prj_p',
      kind: AppJobKinds.projectReload,
      subjectId: 'prj_p',
      title: 'Перезагрузка проекта…',
      subtitle: 'Скачивание образа',
      status: AppJobStatus.running,
      startedAt: started,
      deadlineAt: started.add(const Duration(minutes: 10)),
      updatedAt: started,
    );
    await prefs.setString(
      'app_jobs_v1',
      '[{"id":"${persisted.id}","kind":"${persisted.kind}","subjectId":"${persisted.subjectId}","title":"${persisted.title}","subtitle":"${persisted.subtitle}","status":"running","startedAt":"${persisted.startedAt.toIso8601String()}","deadlineAt":"${persisted.deadlineAt.toIso8601String()}","updatedAt":"${persisted.updatedAt.toIso8601String()}"}]',
    );

    final store = AppJobStore(prefs: prefs);
    await store.ensureHydrated();
    final job = store.lifecycleFor('prj_p');
    expect(job, isNotNull);
    expect(job!.kind, AppJobKinds.projectReload);
    expect(job.title, 'Перезагрузка проекта…');
    expect(job.subtitle, 'Скачивание образа');
  });

  test('runProjectLaunchJob treats POD_ALREADY_EXISTS as join', () async {
    final store = AppJobStore();
    await store.ensureHydrated();
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
}
