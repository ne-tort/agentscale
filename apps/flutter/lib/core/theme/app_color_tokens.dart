import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_palette.dart';

/// Semantic color aliases for UI. Never use raw [Color] hex in screens/widgets.
@immutable
class AppColorTokens extends ThemeExtension<AppColorTokens> {
  const AppColorTokens({
    required this.primary,
    required this.onPrimary,
    required this.primaryContainer,
    required this.onPrimaryContainer,
    required this.surface,
    required this.onSurface,
    required this.surfaceContainer,
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
  final Color primaryContainer;
  final Color onPrimaryContainer;
  final Color surface;
  final Color onSurface;
  final Color surfaceContainer;
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

  factory AppColorTokens.fromPalette(AppTonePalette p, ColorScheme scheme) {
    return AppColorTokens(
      primary: scheme.primary,
      onPrimary: scheme.onPrimary,
      primaryContainer: scheme.primaryContainer,
      onPrimaryContainer: scheme.onPrimaryContainer,
      surface: p.surface,
      onSurface: p.onSurface,
      surfaceContainer: p.surfaceContainer,
      muted: p.muted,
      border: p.border,
      danger: p.danger,
      onDanger: p.onAccent,
      dangerContainer: p.danger.withValues(alpha: 0.18),
      onDangerContainer: p.danger,
      success: p.success,
      onSuccess: p.onAccent,
      warning: p.warning,
      onWarning: p.onAccent,
      info: p.info,
      onInfo: p.onAccent,
      snackErrorBg: p.danger.withValues(alpha: 0.18),
      snackErrorFg: p.danger,
      snackSuccessBg: p.success.withValues(alpha: 0.15),
      snackSuccessFg: p.success,
      snackInfoBg: p.info.withValues(alpha: 0.12),
      snackInfoFg: p.info,
    );
  }

  @override
  AppColorTokens copyWith({
    Color? primary,
    Color? onPrimary,
    Color? primaryContainer,
    Color? onPrimaryContainer,
    Color? surface,
    Color? onSurface,
    Color? surfaceContainer,
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
      primaryContainer: primaryContainer ?? this.primaryContainer,
      onPrimaryContainer: onPrimaryContainer ?? this.onPrimaryContainer,
      surface: surface ?? this.surface,
      onSurface: onSurface ?? this.onSurface,
      surfaceContainer: surfaceContainer ?? this.surfaceContainer,
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
      primaryContainer: Color.lerp(primaryContainer, other.primaryContainer, t)!,
      onPrimaryContainer: Color.lerp(onPrimaryContainer, other.onPrimaryContainer, t)!,
      surface: Color.lerp(surface, other.surface, t)!,
      onSurface: Color.lerp(onSurface, other.onSurface, t)!,
      surfaceContainer: Color.lerp(surfaceContainer, other.surfaceContainer, t)!,
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
  AppColorTokens get appColors {
    final ext = Theme.of(this).extension<AppColorTokens>();
    if (ext != null) return ext;
    final p = AppPalette.light;
    return AppColorTokens.fromPalette(p, ColorScheme.fromSeed(seedColor: p.seed));
  }
}
