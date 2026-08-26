import 'package:flutter/material.dart';

import 'package:prodavan/core/widgets/app_snack_bar.dart';

/// Preview-mode stub actions — laconic snack only.
abstract final class PreviewStub {
  static void run(BuildContext context, String label) {
    final word = _oneWord(label);
    AppSnackBar.info(context, word);
  }

  static String _oneWord(String label) {
    final trimmed = label.trim();
    if (trimmed.isEmpty) return '—';
    final parts = trimmed.split(RegExp(r'\s+'));
    return parts.first;
  }
}
