import 'dart:async';

import 'package:flutter/widgets.dart';

import 'package:prodavan/core/settings/app_settings_controller.dart';

/// Interval choices for settings (seconds). `0` = off.
const kAppAutoRefreshChoicesSeconds = <int>[
  0,
  5,
  10,
  20,
  30,
  40,
  50,
  60,
  5 * 60,
  10 * 60,
];

const kAppAutoRefreshDefaultSeconds = 30;

/// Whether [a] and [b] are deeply equal (Lists/Maps use Dart deep `==`).
bool appRefreshDataEquals(Object? a, Object? b) {
  if (identical(a, b)) return true;
  return a == b;
}

/// Active when mounted, route current, and [TickerMode] enabled
/// (off for offstage [IndexedStack] siblings).
bool appAutoRefreshIsActive(BuildContext context) {
  if (!context.mounted) return false;
  // IndexedStack disables ticker for offstage siblings.
  if (!TickerMode.valuesOf(context).enabled) return false;
  return ModalRoute.of(context)?.isCurrent ?? true;
}

/// Central auto-refresh binder: settings interval + lifecycle + optional route guard.
///
/// Attach in [State.initState], dispose in [State.dispose].
/// [onTick] must be **silent** (no full-page loading spinner / flicker).
class AppAutoRefreshBinder with WidgetsBindingObserver {
  AppAutoRefreshBinder({
    required this.onTick,
    this.isActive,
  });

  final Future<void> Function() onTick;

  /// When false, ticks are skipped (e.g. route not current / offstage tab).
  final bool Function()? isActive;

  Timer? _timer;
  bool _attached = false;
  bool _inFlight = false;
  bool _resumed = true;

  void attach() {
    if (_attached) return;
    _attached = true;
    WidgetsBinding.instance.addObserver(this);
    appSettings.addListener(_reschedule);
    _reschedule();
  }

  void dispose() {
    if (!_attached) return;
    _attached = false;
    _timer?.cancel();
    _timer = null;
    appSettings.removeListener(_reschedule);
    WidgetsBinding.instance.removeObserver(this);
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    switch (state) {
      case AppLifecycleState.resumed:
        _resumed = true;
        _reschedule();
        unawaited(_fire());
      case AppLifecycleState.inactive:
      case AppLifecycleState.hidden:
      case AppLifecycleState.paused:
      case AppLifecycleState.detached:
        _resumed = false;
        _timer?.cancel();
        _timer = null;
    }
  }

  void _reschedule() {
    _timer?.cancel();
    _timer = null;
    if (!_attached || !_resumed) return;
    final sec = appSettings.autoRefreshSeconds;
    if (sec <= 0) return;
    _timer = Timer.periodic(Duration(seconds: sec), (_) {
      unawaited(_fire());
    });
  }

  Future<void> _fire() async {
    if (!_attached || !_resumed || _inFlight) return;
    if (isActive != null && !isActive!()) return;
    _inFlight = true;
    try {
      await onTick();
    } catch (_) {
      // Silent ticks must not surface errors as snacks every N seconds.
    } finally {
      _inFlight = false;
    }
  }
}
