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

String _labelForObserved(Map<String, dynamic>? container, AppLocalizations l10n) {
  final state = containerObservedState(container);
  return switch (state) {
    'preparing' => l10n.containerObservedPreparing,
    'pulling' => l10n.containerObservedPulling,
    'hydrating' => l10n.containerObservedHydrating,
    'provisioning' => l10n.containerObservedProvisioning,
    'starting' => l10n.containerObservedStarting,
    'running' => l10n.containerObservedRunning,
    'failed' => l10n.containerObservedFailed,
    _ => l10n.projectLaunchStartingSnack,
  };
}

/// Launch (or join existing) project pod and poll until settled — navigable-safe.
Future<Map<String, dynamic>?> runProjectLaunchJob({
  required AppJobStore store,
  required ProdavanApi api,
  required String projectId,
  required AppLocalizations l10n,
}) {
  return store.start(
    kind: AppJobKinds.projectLaunch,
    subjectId: projectId,
    label: l10n.projectLaunchStartingSnack,
    run: (ctrl) async {
      try {
        await api.launchProject(projectId);
      } on ProdavanApiException catch (e) {
        if (!_isPodAlreadyExists(e)) rethrow;
      }
      final container = await pollProjectContainerUntilSettled(
        api: api,
        projectId: projectId,
        timeout: const Duration(minutes: 12),
        onTick: (item) {
          ctrl.setLabel(_labelForObserved(item, l10n));
        },
      );
      final failure = containerObservedFailureMessage(container);
      workContext.notifyProjectLifecycleChanged();
      if (failure != null) {
        throw StateError(failure);
      }
    },
  ).then((_) => api.getProjectContainer(projectId));
}

Future<Map<String, dynamic>?> runProjectReloadJob({
  required AppJobStore store,
  required ProdavanApi api,
  required String projectId,
  required AppLocalizations l10n,
}) {
  return store.start(
    kind: AppJobKinds.projectReload,
    subjectId: projectId,
    label: l10n.projectReloadSuccess,
    run: (ctrl) async {
      await api.reloadProject(projectId);
      final container = await pollProjectContainerUntilSettled(
        api: api,
        projectId: projectId,
        timeout: const Duration(minutes: 12),
        onTick: (item) {
          ctrl.setLabel(_labelForObserved(item, l10n));
        },
      );
      final failure = containerObservedFailureMessage(container);
      workContext.notifyProjectLifecycleChanged();
      if (failure != null) {
        throw StateError(failure);
      }
    },
  ).then((_) => api.getProjectContainer(projectId));
}

Future<Map<String, dynamic>?> runProjectResumeJob({
  required AppJobStore store,
  required ProdavanApi api,
  required String projectId,
  required AppLocalizations l10n,
}) {
  return store.start(
    kind: AppJobKinds.projectResume,
    subjectId: projectId,
    label: l10n.projectResumeStartingSnack,
    run: (ctrl) async {
      try {
        await api.resumeProject(projectId);
      } on ProdavanApiException catch (e) {
        if (e.statusCode != 422 ||
            !e.body.toLowerCase().contains('not paused')) {
          rethrow;
        }
      }
      final container = await pollProjectContainerUntilSettled(
        api: api,
        projectId: projectId,
        timeout: const Duration(minutes: 12),
        onTick: (item) {
          ctrl.setLabel(_labelForObserved(item, l10n));
        },
      );
      final failure = containerObservedFailureMessage(container);
      workContext.notifyProjectLifecycleChanged();
      if (failure != null) {
        throw StateError(failure);
      }
    },
  ).then((_) => api.getProjectContainer(projectId));
}
