import 'package:flutter/material.dart';

/// Canonical right-chevron for subpage affordance (size aligned with prefs).
class AppTrailingChevron extends StatelessWidget {
  const AppTrailingChevron({super.key, this.size = 22});

  final double size;

  @override
  Widget build(BuildContext context) {
    return Icon(Icons.chevron_right_rounded, size: size);
  }
}
