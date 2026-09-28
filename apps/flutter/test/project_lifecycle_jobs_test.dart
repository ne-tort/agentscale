import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/containers/project_container_poll.dart';
import 'package:prodavan/core/jobs/app_job_store.dart';
import 'package:prodavan/core/jobs/project_lifecycle_jobs.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/l10n/app_localizations_ru.dart';

class _FakeApi extends Fake implements ProdavanApi {
  _FakeApi(this.container);

  Map<String, dynamic> container;
  Object? resumeError;
  Object? pauseError;
  int resumeCalls = 0;
  int pauseCalls = 0;

  @override
  Future<Map<String, dynamic>> resumeProject(String projectId) async {
    resumeCalls += 1;
    final err = resumeError;
    if (err != null) throw err;
    return {'id': projectId, 'status': 'active'};
  }

  @override
  Future<Map<String, dynamic>> pauseProject(String projectId) async {
    pauseCalls += 1;
    final err = pauseError;
    if (err != null) throw err;
    return {'id': projectId, 'status': 'paused'};
  }

  @override
  Future<Map<String, dynamic>> getProjectContainer(String projectId) async {
    return Map<String, dynamic>.from(container);
  }
}

void main() {
  final l10n = AppLocalizationsRu();

  setUp(() {
    SharedPreferences.setMockInitialValues({});
  });

  group('runProjectResumeJob', () {
    test('failed container restores ContainerObservedFailureException with reason', () async {
      final store = AppJobStore();
      final api = _FakeApi(const {
        'observed_state': 'failed',
        'runtime': {'observed_state': 'failed', 'last_error': 'ImagePullBackOff'},
      });

      Object? caught;
      try {
        await runProjectResumeJob(
          store: store,
          api: api,
          projectId: 'prj_fail',
          l10n: l10n,
        );
      } catch (e) {
        caught = e;
      }

      expect(caught, isA<ContainerObservedFailureException>());
      expect((caught! as ContainerObservedFailureException).reason, 'ImagePullBackOff');

      final job = store.bySubject(
        kind: AppJobKinds.projectResume,
        subjectId: 'prj_fail',
      )!;
      expect(job.status, AppJobStatus.failed);
      expect(job.errorKind, AppJobErrorKind.containerObservedFailure);
      expect(job.error, 'ImagePullBackOff');

      // The presenter branch the user actually sees (honest reason, not a
      // generic "something went wrong").
      final presented = AppErrors.present(caught, l10n);
      expect(presented.display, l10n.projectAgentFailedToStart('ImagePullBackOff'));
      expect(presented.diagnostic, 'ImagePullBackOff');
    });

    test('suspended first tick does not settle the resume poll', () async {
      final store = AppJobStore();
      // Emulate the agent-sandbox resume: first ticks still report suspended.
      final api = _SeqResumeApi(const [
        {'observed_state': 'suspended'},
        {'observed_state': 'running'},
      ]);
      final container = await runProjectResumeJob(
        store: store,
        api: api,
        projectId: 'prj_seq',
        l10n: l10n,
      );
      expect(api.containerCalls, greaterThan(1));
      expect(container?['observed_state'], 'running');
    });

    test('timeout restores a localized TimeoutException', () async {
      final store = AppJobStore();
      final api = _FakeApi(const {'observed_state': 'provisioning'});

      Object? caught;
      try {
        await runProjectResumeJob(
          store: store,
          api: api,
          projectId: 'prj_to',
          l10n: l10n,
          timeout: const Duration(milliseconds: 100),
        );
      } catch (e) {
        caught = e;
      }

      expect(caught, isA<TimeoutException>());
      final job = store.bySubject(
        kind: AppJobKinds.projectResume,
        subjectId: 'prj_to',
      )!;
      expect(job.status, AppJobStatus.failed);
      expect(job.errorKind, AppJobErrorKind.timeout);

      final presented = AppErrors.present(caught!, l10n);
      expect(presented.display, l10n.containerPollTimeout);
    });

    test('422 not-paused is tolerated and the poll is joined', () async {
      final store = AppJobStore();
      final api = _FakeApi(const {'observed_state': 'running'})
        ..resumeError = ProdavanApiException(
          422,
          '{"detail":"project pod is not paused"}',
        );

      final container = await runProjectResumeJob(
        store: store,
        api: api,
        projectId: 'prj_422',
        l10n: l10n,
      );
      expect(api.resumeCalls, 1);
      expect(container?['observed_state'], 'running');
      expect(
        store.bySubject(kind: AppJobKinds.projectResume, subjectId: 'prj_422')!.status,
        AppJobStatus.succeeded,
      );
    });
  });

  group('runProjectPauseJob', () {
    test('waits for suspended and succeeds', () async {
      final store = AppJobStore();
      final api = _FakeApi(const {'observed_state': 'suspended'});

      final container = await runProjectPauseJob(
        store: store,
        api: api,
        projectId: 'prj_pause',
        l10n: l10n,
      );

      expect(api.pauseCalls, 1);
      expect(container?['observed_state'], 'suspended');
      final job = store.bySubject(
        kind: AppJobKinds.projectPause,
        subjectId: 'prj_pause',
      )!;
      expect(job.status, AppJobStatus.succeeded);
    });

    test('still-running container does not settle a pause job (times out)', () async {
      final store = AppJobStore();
      final api = _FakeApi(const {'observed_state': 'running'});

      Object? caught;
      try {
        await runProjectPauseJob(
          store: store,
          api: api,
          projectId: 'prj_pause_running',
          l10n: l10n,
          timeout: const Duration(milliseconds: 100),
        );
      } catch (e) {
        caught = e;
      }

      expect(caught, isA<TimeoutException>());
      expect(
        store.bySubject(kind: AppJobKinds.projectPause, subjectId: 'prj_pause_running')!.errorKind,
        AppJobErrorKind.timeout,
      );
    });

    test('422 (already not running) is tolerated and the poll is joined', () async {
      final store = AppJobStore();
      final api = _FakeApi(const {'observed_state': 'suspended'})
        ..pauseError = ProdavanApiException(
          422,
          '{"detail":"project container is not running"}',
        );

      final container = await runProjectPauseJob(
        store: store,
        api: api,
        projectId: 'prj_pause_422',
        l10n: l10n,
      );
      expect(api.pauseCalls, 1);
      expect(container?['observed_state'], 'suspended');
    });
  });
}

class _SeqResumeApi extends Fake implements ProdavanApi {
  _SeqResumeApi(this.states);

  final List<Map<String, dynamic>> states;
  int containerCalls = 0;

  @override
  Future<Map<String, dynamic>> resumeProject(String projectId) async {
    return {'id': projectId, 'status': 'active'};
  }

  @override
  Future<Map<String, dynamic>> getProjectContainer(String projectId) async {
    final i = containerCalls < states.length ? containerCalls : states.length - 1;
    containerCalls += 1;
    return Map<String, dynamic>.from(states[i]);
  }
}
