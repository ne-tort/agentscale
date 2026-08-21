import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_radii.dart';

enum AppSnackKind { error, success, info }

/// Unified snack presentation (design-system atom).
abstract final class AppSnackBar {
  static void show(
    BuildContext context, {
    required String message,
    required AppSnackKind kind,
  }) {
    final colors = context.appColors;
    late final Color bg;
    late final Color fg;
    switch (kind) {
      case AppSnackKind.error:
        bg = colors.snackErrorBg;
        fg = colors.snackErrorFg;
      case AppSnackKind.success:
        bg = colors.snackSuccessBg;
        fg = colors.snackSuccessFg;
      case AppSnackKind.info:
        bg = colors.snackInfoBg;
        fg = colors.snackInfoFg;
    }

    final messenger = ScaffoldMessenger.of(context);
    messenger.clearSnackBars();
    messenger.showSnackBar(
      SnackBar(
        content: Text(message, style: TextStyle(color: fg)),
        backgroundColor: bg,
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(borderRadius: AppRadii.borderMd),
      ),
    );
  }
}
