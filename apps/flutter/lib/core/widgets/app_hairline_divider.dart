import 'package:flutter/material.dart';

/// Full-bleed hairline used under section headers / inline-add (not table row lines).
class AppHairlineDivider extends StatelessWidget {
  const AppHairlineDivider({super.key});

  @override
  Widget build(BuildContext context) {
    return const Divider(height: 1, thickness: 1);
  }
}
