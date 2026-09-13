import 'package:flutter/widgets.dart';

import 'package:prodavan/core/jobs/app_job_store.dart';

/// Provides [AppJobStore] below the app root so any page can listen.
class AppJobScope extends InheritedNotifier<AppJobStore> {
  const AppJobScope({
    super.key,
    required AppJobStore store,
    required super.child,
  }) : super(notifier: store);

  static AppJobStore of(BuildContext context) {
    final scope = context.dependOnInheritedWidgetOfExactType<AppJobScope>();
    assert(scope != null, 'AppJobScope not found');
    return scope!.notifier!;
  }

  static AppJobStore? maybeOf(BuildContext context) {
    return context.dependOnInheritedWidgetOfExactType<AppJobScope>()?.notifier;
  }
}
