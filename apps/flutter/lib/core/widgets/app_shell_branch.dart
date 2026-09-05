import 'package:flutter/material.dart';

/// One tab branch inside [AppLayout] — nested [Navigator] keeps shell chrome visible.
class AppShellBranch extends StatefulWidget {
  const AppShellBranch({
    super.key,
    required this.root,
    required this.active,
    this.onSubpageOpenChanged,
    this.navigatorKey,
  });

  final Widget root;
  final bool active;

  /// Called when this branch is [active] and stack depth changes (detail open/close).
  final ValueChanged<bool>? onSubpageOpenChanged;

  /// Optional external key so the shell can push routes into this branch (e.g. chat).
  final GlobalKey<NavigatorState>? navigatorKey;

  @override
  State<AppShellBranch> createState() => _AppShellBranchState();
}

class _AppShellBranchState extends State<AppShellBranch> {
  late final GlobalKey<NavigatorState> _navKey =
      widget.navigatorKey ?? GlobalKey<NavigatorState>();
  late final _StackObserver _observer = _StackObserver(_report);

  @override
  void didUpdateWidget(covariant AppShellBranch oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.active && !oldWidget.active) {
      WidgetsBinding.instance.addPostFrameCallback((_) => _report());
    }
  }

  void _report() {
    if (!widget.active) return;
    final open = _navKey.currentState?.canPop() ?? false;
    widget.onSubpageOpenChanged?.call(open);
  }

  @override
  Widget build(BuildContext context) {
    return Navigator(
      key: _navKey,
      observers: [_observer],
      onGenerateRoute: (_) => MaterialPageRoute<void>(
        builder: (_) => widget.root,
      ),
    );
  }
}

class _StackObserver extends NavigatorObserver {
  _StackObserver(this._onChanged);

  final VoidCallback _onChanged;

  void _notify() => _onChanged();

  @override
  void didPush(Route<dynamic> route, Route<dynamic>? previousRoute) => _notify();

  @override
  void didPop(Route<dynamic> route, Route<dynamic>? previousRoute) => _notify();

  @override
  void didRemove(Route<dynamic> route, Route<dynamic>? previousRoute) => _notify();

  @override
  void didReplace({Route<dynamic>? newRoute, Route<dynamic>? oldRoute}) => _notify();
}
