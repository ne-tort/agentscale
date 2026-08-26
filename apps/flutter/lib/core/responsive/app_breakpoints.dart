import 'package:flutter/material.dart';

/// Central breakpoints — features must not invent their own MediaQuery grids.
abstract final class AppBreakpoints {
  static const double narrowMax = 600;
  static const double mediumMax = 1024;

  /// Extra-wide threshold — extended rail stays on subpages only above this.
  static const double significantlyExpandedMin = 1280;

  /// Max width of the main content column (chrome / rail sit outside this).
  static const double contentMaxWidth = 1000;

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

  static bool isSignificantlyExpanded(BuildContext context) =>
      MediaQuery.sizeOf(context).width >= significantlyExpandedMin;

  /// Extended rail: full width on root pages; compact on subpages unless very wide.
  static bool railExtended(BuildContext context, {required bool subpageOpen}) {
    if (isNarrow(context)) return false;
    if (subpageOpen && !isSignificantlyExpanded(context)) return false;
    return isExpanded(context);
  }
}
