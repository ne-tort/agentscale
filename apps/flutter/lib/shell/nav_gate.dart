import 'package:flutter/material.dart';

/// Manifest-driven navigation gate (stub for I0).
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
    // I2: filter by cabinet manifest capabilities.
    return child;
  }
}
