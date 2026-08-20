import 'package:flutter/material.dart';

import 'package:prodavan/shell/app_scope.dart';

/// Feature flag gate from active cabinet capabilities.
class FeatureGate extends StatelessWidget {
  const FeatureGate({
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
