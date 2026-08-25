import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';

enum AppSnackKind { error, success, info }

/// Unified snack presentation (design-system atom).
abstract final class AppSnackBar {
  static Duration _defaultDuration(AppSnackKind kind) => switch (kind) {
        AppSnackKind.error => const Duration(seconds: 8),
        _ => const Duration(seconds: 3),
      };

  static IconData? _iconFor(AppSnackKind kind) => switch (kind) {
        AppSnackKind.error => Icons.error_outline_rounded,
        AppSnackKind.success => Icons.check_circle_outline_rounded,
        AppSnackKind.info => Icons.info_outline_rounded,
      };

  static void show(
    BuildContext context, {
    required String message,
    required AppSnackKind kind,
    String? rawMessage,
    bool copyOnTap = true,
    Duration? duration,
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

    final copyText = rawMessage ?? message;
    final icon = _iconFor(kind);
    final messenger = ScaffoldMessenger.of(context);
    messenger.clearSnackBars();
    messenger.showSnackBar(
      SnackBar(
        elevation: 2,
        behavior: SnackBarBehavior.fixed,
        backgroundColor: bg,
        padding: EdgeInsets.zero,
        duration: duration ?? _defaultDuration(kind),
        dismissDirection: DismissDirection.down,
        content: Material(
          color: Colors.transparent,
          child: InkWell(
            onTap: copyOnTap
                ? () => Clipboard.setData(ClipboardData(text: copyText))
                : null,
            child: SafeArea(
              top: false,
              minimum: EdgeInsets.zero,
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
                child: Row(
                  children: [
                    if (icon != null) ...[
                      Icon(icon, color: fg, size: 22),
                      const SizedBox(width: 12),
                    ],
                    Expanded(
                      child: Text(
                        message,
                        style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                              color: fg,
                              fontWeight: FontWeight.w500,
                            ),
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }

  static void error(
    BuildContext context,
    String message, {
    String? rawMessage,
    bool copyOnTap = true,
  }) {
    show(
      context,
      message: message,
      kind: AppSnackKind.error,
      rawMessage: rawMessage,
      copyOnTap: copyOnTap,
    );
  }

  static void success(BuildContext context, String message) {
    show(context, message: message, kind: AppSnackKind.success, copyOnTap: false);
  }

  static void info(BuildContext context, String message) {
    show(context, message: message, kind: AppSnackKind.info, copyOnTap: false);
  }
}
