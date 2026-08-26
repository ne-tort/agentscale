import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_spacing.dart';

/// Named layout insets for reuse (beyond the raw [AppSpacing] scale).
///
/// Prefer these over magic numbers when aligning chrome / trailing actions.
abstract final class AppInsets {
  /// Right inset for list & preference trailing icons (chevron, inline `+`).
  ///
  /// Matches [ListTileThemeData.contentPadding] / overview alert rows.
  static const double trailingActionRight = AppSpacing.md;

  /// [EdgeInsets] form of [trailingActionRight].
  static const EdgeInsets trailingActionRightOnly =
      EdgeInsets.only(right: trailingActionRight);

  /// AppBar actions end padding (toolbar chrome).
  static const double appBarActionsRight = AppSpacing.lg;

  /// [EdgeInsets] form of [appBarActionsRight].
  static const EdgeInsets appBarActionsRightOnly =
      EdgeInsets.only(right: appBarActionsRight);
}
