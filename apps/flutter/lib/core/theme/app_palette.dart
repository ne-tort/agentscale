import 'package:flutter/material.dart';

/// Единственное место с raw hex. UI использует только [AppColorTokens] / aliases.
abstract final class AppPalette {
  static const Color seed = Color(0xFF1565C0);
  static const Color success = Color(0xFF2E7D32);
  static const Color warning = Color(0xFFED6C02);
  static const Color info = Color(0xFF0277BD);
  static const Color danger = Color(0xFFC62828);
}
