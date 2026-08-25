import 'dart:convert';

import 'package:flutter/material.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/widgets/app_snack_bar.dart';

/// Parsed error for display vs clipboard copy.
class AppErrorPresentation {
  const AppErrorPresentation({required this.display, required this.raw});

  final String display;
  final String raw;
}

/// Extract user-facing and raw error text from exceptions.
abstract final class AppErrors {
  static AppErrorPresentation parse(Object error) {
    if (error is ProdavanApiException) {
      final raw = error.body;
      final display = _displayFromApiBody(error.statusCode, raw);
      return AppErrorPresentation(display: display, raw: raw);
    }
    final raw = error.toString();
    return AppErrorPresentation(display: raw, raw: raw);
  }

  static String _displayFromApiBody(int statusCode, String body) {
    final trimmed = body.trim();
    if (trimmed.isEmpty) return 'HTTP $statusCode';
    if (trimmed.startsWith('<!DOCTYPE') || trimmed.startsWith('<html')) {
      return 'HTTP $statusCode';
    }
    try {
      final decoded = jsonDecode(trimmed);
      if (decoded is Map) {
        final detail = decoded['detail'];
        if (detail is String && detail.isNotEmpty) return detail;
        final title = decoded['title'];
        if (title is String && title.isNotEmpty) return title;
        final message = decoded['message'];
        if (message is String && message.isNotEmpty) return message;
      }
    } catch (_) {}
    if (trimmed.length > 200) return '${trimmed.substring(0, 200)}…';
    return trimmed;
  }

  static void showSnack(BuildContext context, Object error) {
    final parsed = parse(error);
    AppSnackBar.error(
      context,
      parsed.display,
      rawMessage: parsed.raw,
    );
  }
}
