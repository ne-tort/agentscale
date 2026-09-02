import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';

/// Severity for [AppSnackBar] — visuals from [AppColorTokens] / [ColorScheme].
enum AppSnackSeverity { info, success, warning, error }

/// Visual overrides for a single snackbar instance.
class AppSnackStyle {
  const AppSnackStyle({
    this.backgroundColor,
    this.foregroundColor,
    this.icon,
    this.iconColor,
    this.elevation,
    this.showCloseIcon,
  });

  final Color? backgroundColor;
  final Color? foregroundColor;
  final IconData? icon;
  final Color? iconColor;
  final double? elevation;
  final bool? showCloseIcon;

  AppSnackStyle merge(AppSnackStyle? other) {
    if (other == null) return this;
    return AppSnackStyle(
      backgroundColor: other.backgroundColor ?? backgroundColor,
      foregroundColor: other.foregroundColor ?? foregroundColor,
      icon: other.icon ?? icon,
      iconColor: other.iconColor ?? iconColor,
      elevation: other.elevation ?? elevation,
      showCloseIcon: other.showCloseIcon ?? showCloseIcon,
    );
  }

  /// Theme-aware defaults (Hiddify snack look via tokens, no raw hex).
  static AppSnackStyle forSeverity(
    BuildContext context,
    AppSnackSeverity severity,
  ) {
    final tokens = context.appColors;
    return switch (severity) {
      AppSnackSeverity.info => AppSnackStyle(
          backgroundColor: tokens.primaryContainer,
          foregroundColor: tokens.onPrimaryContainer,
          icon: Icons.info_outline_rounded,
          iconColor: tokens.onPrimaryContainer,
        ),
      AppSnackSeverity.success => AppSnackStyle(
          backgroundColor: tokens.success,
          foregroundColor: tokens.onSuccess,
          icon: Icons.check_circle_outline_rounded,
          iconColor: tokens.onSuccess,
        ),
      AppSnackSeverity.warning => AppSnackStyle(
          backgroundColor: tokens.warning,
          foregroundColor: tokens.onWarning,
          icon: Icons.warning_amber_rounded,
          iconColor: tokens.onWarning,
        ),
      AppSnackSeverity.error => AppSnackStyle(
          backgroundColor: tokens.danger,
          foregroundColor: tokens.onDanger,
          icon: Icons.error_outline_rounded,
          iconColor: tokens.onDanger,
        ),
    };
  }
}

/// Edge-to-edge in-app snackbar (Hiddify `AppSnackbar` adapted to Prodavan tokens).
abstract final class AppSnackBar {
  static Duration defaultDuration(AppSnackSeverity severity) => switch (severity) {
        AppSnackSeverity.error => const Duration(seconds: 8),
        AppSnackSeverity.warning => const Duration(seconds: 6),
        _ => const Duration(seconds: 3),
      };

  static void info(
    BuildContext context,
    String message, {
    Duration? duration,
    bool copyOnTap = false,
  }) =>
      show(
        context,
        message: message,
        severity: AppSnackSeverity.info,
        duration: duration,
        copyOnTap: copyOnTap,
      );

  static void success(
    BuildContext context,
    String message, {
    Duration? duration,
  }) =>
      show(
        context,
        message: message,
        severity: AppSnackSeverity.success,
        duration: duration,
        copyOnTap: false,
      );

  static void warning(
    BuildContext context,
    String message, {
    Duration? duration,
    bool copyOnTap = false,
  }) =>
      show(
        context,
        message: message,
        severity: AppSnackSeverity.warning,
        duration: duration,
        copyOnTap: copyOnTap,
      );

  static void error(
    BuildContext context,
    String message, {
    String? rawMessage,
    Duration? duration,
    bool copyOnTap = true,
  }) =>
      show(
        context,
        message: message,
        severity: AppSnackSeverity.error,
        rawMessage: rawMessage,
        duration: duration,
        copyOnTap: copyOnTap,
      );

  static void show(
    BuildContext context, {
    required String message,
    AppSnackSeverity severity = AppSnackSeverity.info,
    String? rawMessage,
    AppSnackStyle? style,
    bool copyOnTap = true,
    bool clearOthers = true,
    Duration? duration,
  }) {
    if (!context.mounted) return;
    final messenger = ScaffoldMessenger.maybeOf(context);
    if (messenger == null) return;

    final resolved =
        AppSnackStyle.forSeverity(context, severity).merge(style);
    final fg = resolved.foregroundColor ??
        (severity == AppSnackSeverity.info
            ? context.appColors.onPrimaryContainer
            : Theme.of(context).colorScheme.onInverseSurface);
    final bg = resolved.backgroundColor ??
        (severity == AppSnackSeverity.info
            ? context.appColors.primaryContainer
            : Theme.of(context).colorScheme.inverseSurface);
    final copyText = rawMessage ?? message;

    if (clearOthers) messenger.clearSnackBars();
    messenger.showSnackBar(
      SnackBar(
        elevation: resolved.elevation ?? 2,
        behavior: SnackBarBehavior.fixed,
        shape: const RoundedRectangleBorder(borderRadius: BorderRadius.zero),
        backgroundColor: bg,
        padding: EdgeInsets.zero,
        duration: duration ?? defaultDuration(severity),
        dismissDirection: DismissDirection.down,
        showCloseIcon: resolved.showCloseIcon ?? false,
        closeIconColor: fg,
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
                padding:
                    const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
                child: Row(
                  children: [
                    if (resolved.icon != null) ...[
                      Icon(
                        resolved.icon,
                        color: resolved.iconColor ?? fg,
                        size: 22,
                      ),
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
}
