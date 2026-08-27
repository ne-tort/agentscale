import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';

/// Severity for [AppStatusBanner] — theme colors come from [AppColorTokens].
enum AppStatusSeverity {
  info,
  success,
  warning,
  error,
  critical,
}

/// Status strip (Hiddify `StatusBanner` look) — no raw hex; all colors from tokens.
class AppStatusBanner extends StatelessWidget {
  const AppStatusBanner({
    super.key,
    required this.severity,
    this.message,
    this.messageChild,
    this.title,
    this.margin = const EdgeInsets.fromLTRB(
      AppSpacing.lg,
      AppSpacing.md,
      AppSpacing.lg,
      AppSpacing.sm,
    ),
    this.outlined = false,
  }) : assert(message != null || messageChild != null);

  final AppStatusSeverity severity;
  final String? message;
  final Widget? messageChild;
  final String? title;
  final EdgeInsetsGeometry margin;

  /// Transparent background with border in the severity foreground color.
  final bool outlined;

  @override
  Widget build(BuildContext context) {
    final tokens = context.appColors;
    final (fg, bg, icon) = _palette(tokens);
    final borderRadius = BorderRadius.circular(12);
    final useOutlined = _useOutlined;

    return Padding(
      padding: margin,
      child: Material(
        color: useOutlined ? Colors.transparent : bg,
        shape: RoundedRectangleBorder(
          borderRadius: borderRadius,
          side: useOutlined ? BorderSide(color: fg, width: 1.2) : BorderSide.none,
        ),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(icon, color: fg, size: 22),
              const SizedBox(width: AppSpacing.md),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    if (title != null && title!.isNotEmpty) ...[
                      Text(
                        title!,
                        style: Theme.of(context).textTheme.titleSmall?.copyWith(
                              color: fg,
                              fontWeight: FontWeight.w600,
                            ),
                      ),
                      const SizedBox(height: AppSpacing.xs),
                    ],
                    if (messageChild != null)
                      DefaultTextStyle(
                        style: Theme.of(context)
                            .textTheme
                            .bodyMedium!
                            .copyWith(color: fg),
                        child: messageChild!,
                      )
                    else
                      Text(
                        message ?? '',
                        style: Theme.of(context)
                            .textTheme
                            .bodyMedium
                            ?.copyWith(color: fg),
                      ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  (Color, Color, IconData) _palette(AppColorTokens tokens) {
    return switch (severity) {
      AppStatusSeverity.info => (
          tokens.info,
          tokens.info.withValues(alpha: 0.14),
          Icons.info_outline_rounded,
        ),
      AppStatusSeverity.success => (
          tokens.success,
          tokens.success.withValues(alpha: 0.14),
          Icons.check_circle_outline_rounded,
        ),
      AppStatusSeverity.warning => (
          tokens.warning,
          tokens.warning.withValues(alpha: 0.14),
          Icons.warning_amber_rounded,
        ),
      AppStatusSeverity.error => (
          tokens.danger,
          tokens.dangerContainer,
          Icons.error_outline_rounded,
        ),
      AppStatusSeverity.critical => (
          tokens.onDanger,
          tokens.danger,
          Icons.report_rounded,
        ),
    };
  }

  bool get _useOutlined {
    if (outlined) return true;
    // Default: warning/info/success are outlined (transparent + border).
    return severity == AppStatusSeverity.warning ||
        severity == AppStatusSeverity.info ||
        severity == AppStatusSeverity.success;
  }
}
