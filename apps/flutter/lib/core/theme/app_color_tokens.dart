import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_palette.dart';

/// Semantic color aliases for UI. Never use raw [Color] hex in screens/widgets.
@immutable
class AppColorTokens extends ThemeExtension<AppColorTokens> {
  const AppColorTokens({
    required this.primary,
    required this.onPrimary,
    required this.surface,
    required this.onSurface,
    required this.muted,
    required this.border,
    required this.danger,
    required this.onDanger,
    required this.dangerContainer,
    required this.onDangerContainer,
    required this.success,
    required this.onSuccess,
    required this.warning,
    required this.onWarning,
    required this.info,
    required this.onInfo,
    required this.snackErrorBg,
    required this.snackErrorFg,
    required this.snackSuccessBg,
    required this.snackSuccessFg,
    required this.snackInfoBg,
    required this.snackInfoFg,
  });

  final Color primary;
  final Color onPrimary;
  final Color surface;
  final Color onSurface;
  final Color muted;
  final Color border;
  final Color danger;
  final Color onDanger;
  final Color dangerContainer;
  final Color onDangerContainer;
  final Color success;
  final Color onSuccess;
  final Color warning;
  final Color onWarning;
  final Color info;
  final Color onInfo;
  final Color snackErrorBg;
  final Color snackErrorFg;
  final Color snackSuccessBg;
  final Color snackSuccessFg;
  final Color snackInfoBg;
  final Color snackInfoFg;

  factory AppColorTokens.fromScheme(ColorScheme scheme) {
    return AppColorTokens(
      primary: scheme.primary,
      onPrimary: scheme.onPrimary,
      surface: scheme.surface,
      onSurface: scheme.onSurface,
      muted: scheme.onSurfaceVariant,
      border: scheme.outlineVariant,
      danger: scheme.error,
      onDanger: scheme.onError,
      dangerContainer: scheme.errorContainer,
      onDangerContainer: scheme.onErrorContainer,
      success: AppPalette.success,
      onSuccess: scheme.onPrimary,
      warning: AppPalette.warning,
      onWarning: scheme.onPrimary,
      info: AppPalette.info,
      onInfo: scheme.onPrimary,
      snackErrorBg: scheme.errorContainer,
      snackErrorFg: scheme.onErrorContainer,
      snackSuccessBg: AppPalette.success.withValues(alpha: 0.15),
      snackSuccessFg: AppPalette.success,
      snackInfoBg: AppPalette.info.withValues(alpha: 0.12),
      snackInfoFg: AppPalette.info,
    );
  }

  @override
  AppColorTokens copyWith({
    Color? primary,
    Color? onPrimary,
    Color? surface,
    Color? onSurface,
    Color? muted,
    Color? border,
    Color? danger,
    Color? onDanger,
    Color? dangerContainer,
    Color? onDangerContainer,
    Color? success,
    Color? onSuccess,
    Color? warning,
    Color? onWarning,
    Color? info,
    Color? onInfo,
    Color? snackErrorBg,
    Color? snackErrorFg,
    Color? snackSuccessBg,
    Color? snackSuccessFg,
    Color? snackInfoBg,
    Color? snackInfoFg,
  }) {
    return AppColorTokens(
      primary: primary ?? this.primary,
      onPrimary: onPrimary ?? this.onPrimary,
      surface: surface ?? this.surface,
      onSurface: onSurface ?? this.onSurface,
      muted: muted ?? this.muted,
      border: border ?? this.border,
      danger: danger ?? this.danger,
      onDanger: onDanger ?? this.onDanger,
      dangerContainer: dangerContainer ?? this.dangerContainer,
      onDangerContainer: onDangerContainer ?? this.onDangerContainer,
      success: success ?? this.success,
      onSuccess: onSuccess ?? this.onSuccess,
      warning: warning ?? this.warning,
      onWarning: onWarning ?? this.onWarning,
      info: info ?? this.info,
      onInfo: onInfo ?? this.onInfo,
      snackErrorBg: snackErrorBg ?? this.snackErrorBg,
      snackErrorFg: snackErrorFg ?? this.snackErrorFg,
      snackSuccessBg: snackSuccessBg ?? this.snackSuccessBg,
      snackSuccessFg: snackSuccessFg ?? this.snackSuccessFg,
      snackInfoBg: snackInfoBg ?? this.snackInfoBg,
      snackInfoFg: snackInfoFg ?? this.snackInfoFg,
    );
  }

  @override
  AppColorTokens lerp(ThemeExtension<AppColorTokens>? other, double t) {
    if (other is! AppColorTokens) return this;
    return AppColorTokens(
      primary: Color.lerp(primary, other.primary, t)!,
      onPrimary: Color.lerp(onPrimary, other.onPrimary, t)!,
      surface: Color.lerp(surface, other.surface, t)!,
      onSurface: Color.lerp(onSurface, other.onSurface, t)!,
      muted: Color.lerp(muted, other.muted, t)!,
      border: Color.lerp(border, other.border, t)!,
      danger: Color.lerp(danger, other.danger, t)!,
      onDanger: Color.lerp(onDanger, other.onDanger, t)!,
      dangerContainer: Color.lerp(dangerContainer, other.dangerContainer, t)!,
      onDangerContainer: Color.lerp(onDangerContainer, other.onDangerContainer, t)!,
      success: Color.lerp(success, other.success, t)!,
      onSuccess: Color.lerp(onSuccess, other.onSuccess, t)!,
      warning: Color.lerp(warning, other.warning, t)!,
      onWarning: Color.lerp(onWarning, other.onWarning, t)!,
      info: Color.lerp(info, other.info, t)!,
      onInfo: Color.lerp(onInfo, other.onInfo, t)!,
      snackErrorBg: Color.lerp(snackErrorBg, other.snackErrorBg, t)!,
      snackErrorFg: Color.lerp(snackErrorFg, other.snackErrorFg, t)!,
      snackSuccessBg: Color.lerp(snackSuccessBg, other.snackSuccessBg, t)!,
      snackSuccessFg: Color.lerp(snackSuccessFg, other.snackSuccessFg, t)!,
      snackInfoBg: Color.lerp(snackInfoBg, other.snackInfoBg, t)!,
      snackInfoFg: Color.lerp(snackInfoFg, other.snackInfoFg, t)!,
    );
  }
}

extension AppColorTokensX on BuildContext {
  AppColorTokens get appColors =>
      Theme.of(this).extension<AppColorTokens>() ??
      AppColorTokens.fromScheme(Theme.of(this).colorScheme);
}
