import 'package:flutter/material.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/containers/project_container_poll.dart';
import 'package:prodavan/core/jobs/app_job_store.dart';
import 'package:prodavan/core/jobs/project_lifecycle_jobs.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_snack_bar.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Lifecycle actions that change the container's power state.
enum ProjectWakeAction { launch, reload, resume, pause }

/// Unified wake-flow presenter shared by the settings / container /
/// management / workspace pages:
///
/// 1. runs the lifecycle job for [action] (deduped via [AppJobStore],
///    polling with the correct waitFor set per action),
/// 2. shows an honest outcome — a typed error snack (container last_error /
///    localized timeout via [AppErrors]) or the optional [successSnack],
/// 3. always calls [onSettled] afterwards so the page refreshes its state.
///
/// Behavior contract: the success snack is shown only after the poll has
/// settled without failure — never prematurely, never on top of an error.
Future<void> runProjectWakeFlow({
  required BuildContext context,
  required ProjectWakeAction action,
  required AppJobStore store,
  required ProdavanApi api,
  required String projectId,
  String? Function(AppLocalizations l10n)? successSnack,
  Future<void> Function()? onSettled,
}) async {
  final l10n = AppLocalizations.of(context);
  Object? error;
  Map<String, dynamic>? container;
  try {
    container = await switch (action) {
      ProjectWakeAction.launch => runProjectLaunchJob(
          store: store,
          api: api,
          projectId: projectId,
          l10n: l10n,
        ),
      ProjectWakeAction.reload => runProjectReloadJob(
          store: store,
          api: api,
          projectId: projectId,
          l10n: l10n,
        ),
      ProjectWakeAction.resume => runProjectResumeJob(
          store: store,
          api: api,
          projectId: projectId,
          l10n: l10n,
        ),
      ProjectWakeAction.pause => runProjectPauseJob(
          store: store,
          api: api,
          projectId: projectId,
          l10n: l10n,
        ),
    };
  } catch (e) {
    error = e;
  }
  if (!context.mounted) return;
  if (error != null) {
    AppErrors.showSnack(context, error);
  } else {
    // Backstop: the job succeeded but the container still carries an error.
    final failure = containerObservedFailureMessage(container);
    if (failure != null) {
      AppErrors.showSnack(context, ContainerObservedFailureException(failure));
    } else {
      final message = successSnack?.call(l10n);
      if (message != null) AppSnackBar.success(context, message);
    }
  }
  if (context.mounted) {
    await onSettled?.call();
  }
}
