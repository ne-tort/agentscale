import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_palette.dart';
import 'package:prodavan/core/theme/app_radii.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/theme/app_typography.dart';

class AppTheme {
  const AppTheme._();

  static ThemeData forMode(AppThemeMode mode) {
    final palette = AppPalette.forMode(mode);
    final brightness = mode == AppThemeMode.light ? Brightness.light : Brightness.dark;
    final scheme = ColorScheme.fromSeed(
      seedColor: palette.seed,
      brightness: brightness,
      error: palette.danger,
      surface: palette.surface,
      onSurface: palette.onSurface,
    ).copyWith(
      surfaceContainerLow: palette.surfaceContainer,
      surfaceContainerHighest: palette.surfaceContainer,
      onSurfaceVariant: palette.muted,
      outlineVariant: palette.border,
    );
    final tokens = AppColorTokens.fromPalette(palette, scheme);
    return ThemeData(
      useMaterial3: true,
      brightness: brightness,
      colorScheme: scheme,
      textTheme: AppTypography.textTheme(scheme),
      scaffoldBackgroundColor: tokens.surface,
      extensions: [tokens],
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: tokens.surfaceContainer.withValues(alpha: 0.55),
        border: OutlineInputBorder(borderRadius: AppRadii.borderMd),
        enabledBorder: OutlineInputBorder(
          borderRadius: AppRadii.borderMd,
          borderSide: BorderSide(color: tokens.border),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: AppRadii.borderMd,
          borderSide: BorderSide(color: tokens.primary, width: 1.5),
        ),
        contentPadding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.md,
          vertical: AppSpacing.md,
        ),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          minimumSize: const Size.fromHeight(48),
          shape: RoundedRectangleBorder(borderRadius: AppRadii.borderMd),
          padding: const EdgeInsets.symmetric(
            horizontal: AppSpacing.lg,
            vertical: AppSpacing.md,
          ),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          minimumSize: const Size.fromHeight(48),
          shape: RoundedRectangleBorder(borderRadius: AppRadii.borderMd),
        ),
      ),
      cardTheme: CardThemeData(
        elevation: 0,
        color: tokens.surfaceContainer,
        shape: RoundedRectangleBorder(
          borderRadius: AppRadii.borderLg,
          side: BorderSide(color: tokens.border.withValues(alpha: 0.6)),
        ),
        margin: EdgeInsets.zero,
      ),
      appBarTheme: AppBarTheme(
        centerTitle: false,
        backgroundColor: tokens.surface,
        foregroundColor: tokens.onSurface,
        elevation: 0,
        scrolledUnderElevation: 1,
        actionsPadding: const EdgeInsets.only(right: AppSpacing.lg),
      ),
      navigationRailTheme: NavigationRailThemeData(
        backgroundColor: tokens.surface,
        indicatorColor: tokens.primary.withValues(alpha: 0.14),
        selectedIconTheme: IconThemeData(color: tokens.primary),
        unselectedIconTheme: IconThemeData(color: tokens.muted),
        selectedLabelTextStyle: TextStyle(color: tokens.primary, fontSize: 12),
        unselectedLabelTextStyle: TextStyle(color: tokens.muted, fontSize: 12),
      ),
      navigationBarTheme: NavigationBarThemeData(
        backgroundColor: tokens.surface,
        indicatorColor: tokens.primary.withValues(alpha: 0.14),
      ),
      snackBarTheme: SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(borderRadius: AppRadii.borderMd),
      ),
    );
  }

  /// Compatibility getters.
  static ThemeData get light => forMode(AppThemeMode.light);
  static ThemeData get dark => forMode(AppThemeMode.dark);
  static ThemeData get ultraDark => forMode(AppThemeMode.ultraDark);
}
