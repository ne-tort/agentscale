import 'dart:async';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/containers/container_runtime_presenter.dart';
import 'package:prodavan/core/containers/project_container_poll.dart';
import 'package:prodavan/core/jobs/app_job_store.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/l10n/app_localizations.dart';

bool _isPodAlreadyExists(Object error) {
  if (error is! ProdavanApiException) return false;
  if (error.statusCode != 409) return false;
  final body = error.body.toLowerCase();
  return body.contains('pod_already_exists') ||
      body.contains('project pod already exists');
}

String phaseSubtitle(Map<String, dynamic>? container, AppLocalizations l10n) {
  final state = containerObservedState(container);
  return switch (state) {
    'preparing' => l10n.containerObservedPreparing,
    'pulling' => l10n.containerObservedPulling,
    'hydrating' => l10n.containerObservedHydrating,
    'provisioning' => l10n.containerObservedProvisioning,
    'starting' => l10n.containerObservedStarting,
    'resuming' => l10n.containerObservedResuming,
    'suspended' => l10n.containerObservedSuspended,
    'pausing' => l10n.containerObservedPausing,
    'running' => l10n.containerObservedRunning,
    'degraded' => l10n.containerObservedDegraded,
    'failed' => l10n.containerObservedFailed,
    'paused' => l10n.containerObservedSuspended,
    'absent' => l10n.containerObservedAbsent,
    _ => l10n.projectLaunchStartingSnack,
  };
}

Future<void> _pollUntilSettledOrThrow({
  required ProdavanApi api,
  required String projectId,
  required AppJobController ctrl,
  required AppLocalizations l10n,
  required Duration timeout,
}) async {
  final container = await pollProjectContainerUntilSettled(
    api: api,
    projectId: projectId,
    timeout: timeout,
    onTick: (item) => ctrl.setSubtitle(phaseSubtitle(item, l10n)),
  );
  if (!containerObservedSettled(container)) {
    throw TimeoutException(
      l10n.containerPollTimeout,
      timeout,
    );
  }
  final failure = containerObservedFailureMessage(container);
  if (failure != null) {
    throw StateError(failure);
  }
}

Future<Map<String, dynamic>?> _afterJob(
  Future<AppJob> jobFuture,
  ProdavanApi api,
  String projectId,
) async {
  final job = await jobFuture;
  if (job.status == AppJobStatus.failed) {
    throw StateError(job.error ?? 'job failed');
  }
  return api.getProjectContainer(projectId);
}

/// Launch (or join existing pod) and poll until settled.
Future<Map<String, dynamic>?> runProjectLaunchJob({
  required AppJobStore store,
  required ProdavanApi api,
  required String projectId,
  required AppLocalizations l10n,
  bool invokeApi = true,
}) {
  return _afterJob(
    store.start(
      kind: AppJobKinds.projectLaunch,
      subjectId: projectId,
      title: l10n.projectLaunchInProgress,
      subtitle: l10n.projectLaunchStartingSnack,
      timeout: containerPollDefaultTimeout,
      run: (ctrl) async {
        if (invokeApi) {
          try {
            await api.launchProject(projectId);
          } on ProdavanApiException catch (e) {
            if (!_isPodAlreadyExists(e)) rethrow;
          }
        }
        await _pollUntilSettledOrThrow(
          api: api,
          projectId: projectId,
          ctrl: ctrl,
          l10n: l10n,
          timeout: ctrl.remaining,
        );
        workContext.notifyProjectLifecycleChanged();
      },
    ),
    api,
    projectId,
  );
}

Future<Map<String, dynamic>?> runProjectReloadJob({
  required AppJobStore store,
  required ProdavanApi api,
  required String projectId,
  required AppLocalizations l10n,
  bool invokeApi = true,
}) {
  return _afterJob(
    store.start(
      kind: AppJobKinds.projectReload,
      subjectId: projectId,
      title: l10n.projectReloadInProgress,
      subtitle: l10n.projectLaunchStartingSnack,
      timeout: containerPollDefaultTimeout,
      run: (ctrl) async {
        if (invokeApi) {
          await api.reloadProject(projectId);
        }
        await _pollUntilSettledOrThrow(
          api: api,
          projectId: projectId,
          ctrl: ctrl,
          l10n: l10n,
          timeout: ctrl.remaining,
        );
        workContext.notifyProjectLifecycleChanged();
      },
    ),
    api,
    projectId,
  );
}

Future<Map<String, dynamic>?> runProjectResumeJob({
  required AppJobStore store,
  required ProdavanApi api,
  required String projectId,
  required AppLocalizations l10n,
  bool invokeApi = true,
}) {
  return _afterJob(
    store.start(
      kind: AppJobKinds.projectResume,
      subjectId: projectId,
      title: l10n.projectResumeInProgress,
      subtitle: l10n.projectResumeStartingSnack,
      timeout: containerPollDefaultTimeout,
      run: (ctrl) async {
        if (invokeApi) {
          try {
            await api.resumeProject(projectId);
          } on ProdavanApiException catch (e) {
            if (e.statusCode != 422 ||
                !e.body.toLowerCase().contains('not paused')) {
              rethrow;
            }
          }
        }
        await _pollUntilSettledOrThrow(
          api: api,
          projectId: projectId,
          ctrl: ctrl,
          l10n: l10n,
          timeout: ctrl.remaining,
        );
        workContext.notifyProjectLifecycleChanged();
      },
    ),
    api,
    projectId,
  );
}

/// After page reload: re-attach poll to a persisted running job (no second API call).
Future<void> resumePersistedLifecycleJob({
  required AppJobStore store,
  required ProdavanApi api,
  required String projectId,
  required AppLocalizations l10n,
}) async {
  await store.ensureHydrated();
  final job = store.lifecycleFor(projectId);
  if (job == null || job.status != AppJobStatus.running) return;
  if (job.isExpired) return;

  switch (job.kind) {
    case AppJobKinds.projectLaunch:
      await runProjectLaunchJob(
        store: store,
        api: api,
        projectId: projectId,
        l10n: l10n,
        invokeApi: false,
      );
    case AppJobKinds.projectReload:
      await runProjectReloadJob(
        store: store,
        api: api,
        projectId: projectId,
        l10n: l10n,
        invokeApi: false,
      );
    case AppJobKinds.projectResume:
      await runProjectResumeJob(
        store: store,
        api: api,
        projectId: projectId,
        l10n: l10n,
        invokeApi: false,
      );
    default:
      break;
  }
}

/// Join backend in-flight start when store has no job (e.g. another device / lost prefs).
Future<void> joinInFlightProjectStart({
  required AppJobStore store,
  required ProdavanApi api,
  required String projectId,
  required AppLocalizations l10n,
}) {
  return runProjectLaunchJob(
    store: store,
    api: api,
    projectId: projectId,
    l10n: l10n,
    invokeApi: false,
  ).then((_) {});
}
