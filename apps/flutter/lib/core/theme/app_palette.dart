import 'package:flutter/material.dart';

/// App appearance modes — not Material [ThemeMode] (needs ultraDark).
enum AppThemeMode {
  light,
  dark,
  ultraDark,
}

/// Raw hex per theme mode. UI must use [AppColorTokens] only.
@immutable
class AppTonePalette {
  const AppTonePalette({
    required this.seed,
    required this.success,
    required this.warning,
    required this.info,
    required this.danger,
    required this.onAccent,
    required this.surface,
    required this.onSurface,
    required this.surfaceContainer,
    required this.muted,
    required this.border,
  });

  final Color seed;
  final Color success;
  final Color warning;
  final Color info;
  final Color danger;
  /// Contrasting ink on solid success/warning/info/danger fills.
  final Color onAccent;
  final Color surface;
  final Color onSurface;
  final Color surfaceContainer;
  final Color muted;
  final Color border;
}

/// Единственное место с raw hex.
abstract final class AppPalette {
  static const light = AppTonePalette(
    seed: Color(0xFF1565C0),
    success: Color(0xFF2E7D32),
    warning: Color(0xFFFF7F00),
    info: Color(0xFF0277BD),
    danger: Color(0xFFC62828),
    onAccent: Color(0xFFFFFFFF),
    surface: Color(0xFFFFFBFE),
    onSurface: Color(0xFF1C1B1F),
    surfaceContainer: Color(0xFFF3EDF7),
    muted: Color(0xFF49454F),
    border: Color(0xFFCAC4D0),
  );

  static const dark = AppTonePalette(
    seed: Color(0xFF90CAF9),
    success: Color(0xFF81C784),
    warning: Color(0xFFFF9933),
    info: Color(0xFF4FC3F7),
    danger: Color(0xFFEF9A9A),
    onAccent: Color(0xFF1C1B1F),
    surface: Color(0xFF1C1B1F),
    onSurface: Color(0xFFE6E1E5),
    surfaceContainer: Color(0xFF2B2930),
    muted: Color(0xFFCAC4D0),
    border: Color(0xFF49454F),
  );

  /// Near-black surfaces, desaturated accents.
  static const ultraDark = AppTonePalette(
    seed: Color(0xFF64B5F6),
    success: Color(0xFF66BB6A),
    warning: Color(0xFFFF9933),
    info: Color(0xFF29B6F6),
    danger: Color(0xFFE57373),
    onAccent: Color(0xFF0A0A0B),
    surface: Color(0xFF0A0A0B),
    onSurface: Color(0xFFE0E0E0),
    surfaceContainer: Color(0xFF141416),
    muted: Color(0xFF9E9E9E),
    border: Color(0xFF2A2A2E),
  );

  static AppTonePalette forMode(AppThemeMode mode) => switch (mode) {
        AppThemeMode.light => light,
        AppThemeMode.dark => dark,
        AppThemeMode.ultraDark => ultraDark,
      };
}
