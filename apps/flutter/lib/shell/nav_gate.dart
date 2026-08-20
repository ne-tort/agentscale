import 'package:flutter/material.dart';

import 'package:prodavan/shell/app_scope.dart';

/// Manifest-driven navigation gate: shows child only if capability enabled.
class NavGate extends StatelessWidget {
  const NavGate({
    super.key,
    required this.capability,
    required this.child,
    this.fallback = const SizedBox.shrink(),
  });

  final String capability;
  final Widget child;
  final Widget fallback;

  @override
  Widget build(BuildContext context) {
    final enabled = AppScope.of(context).isCapabilityEnabled(capability);
    return enabled ? child : fallback;
  }
}
