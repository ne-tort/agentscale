import 'package:flutter/material.dart';

/// Central breakpoints — features must not invent their own MediaQuery grids.
abstract final class AppBreakpoints {
  static const double narrowMax = 600;
  static const double mediumMax = 1024;

  static bool isNarrow(BuildContext context) =>
      MediaQuery.sizeOf(context).width < narrowMax;

  static bool isMedium(BuildContext context) {
    final w = MediaQuery.sizeOf(context).width;
    return w >= narrowMax && w < mediumMax;
  }

  static bool isWide(BuildContext context) =>
      MediaQuery.sizeOf(context).width >= narrowMax;

  static bool isExpanded(BuildContext context) =>
      MediaQuery.sizeOf(context).width >= mediumMax;
}
