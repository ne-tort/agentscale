import 'dart:math' as math;

import 'package:flutter/material.dart';

import 'package:prodavan/core/responsive/app_breakpoints.dart';

/// Pins [child] to the top-left of the available area with
/// [AppBreakpoints.contentMaxWidth], without centering in leftover space.
class AppContentFrame extends StatelessWidget {
  const AppContentFrame({super.key, required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final maxW = constraints.maxWidth;
        final width = maxW.isFinite
            ? math.min(maxW, AppBreakpoints.contentMaxWidth)
            : AppBreakpoints.contentMaxWidth;
        final height = constraints.maxHeight.isFinite ? constraints.maxHeight : null;
        return Align(
          alignment: Alignment.topLeft,
          child: SizedBox(width: width, height: height, child: child),
        );
      },
    );
  }
}
