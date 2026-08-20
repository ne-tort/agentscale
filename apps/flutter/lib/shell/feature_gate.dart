import 'package:flutter/material.dart';

/// Feature flag gate from cabinet profile (stub for I0).
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
    // I2: read active cabinet capabilities from app state.
    return child;
  }
}
