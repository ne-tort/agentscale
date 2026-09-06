import 'dart:async';
import 'dart:convert';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/preferences/app_preference_tile.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_trailing_chevron.dart';

/// Upload file via cabinet content API; stores FileRef map in form state.
///
/// Preference-tile chrome (same row pattern as [AppNavPreference]): tap to pick.
/// When [warnWhenEmpty] and no file — title uses warning color and [subtitle] is omitted.
class FileUploadField extends StatelessWidget {
  const FileUploadField({
    super.key,
    required this.label,
    required this.value,
    required this.cabinetId,
    required this.api,
    required this.onChanged,
    this.readOnly = false,
    this.accept,
    this.subtitle,
    this.warnWhenEmpty = false,
  });

  final String label;
  final dynamic value;
  final String cabinetId;
  final ProdavanApi api;
  final FutureOr<void> Function(Map<String, dynamic>?) onChanged;
  final bool readOnly;
  final String? accept;
  /// Shown only when a file is present and subtitle is non-empty.
  final String? subtitle;
  final bool warnWhenEmpty;

  Map<String, dynamic>? get _ref =>
      value is Map ? Map<String, dynamic>.from(value as Map) : null;

  bool get _hasFile => _ref != null;

  Future<void> _pick(BuildContext context) async {
    final result = await FilePicker.platform.pickFiles(
      withData: true,
      type: FileType.custom,
      allowedExtensions: _extensions(),
    );
    if (result == null || result.files.isEmpty) return;
    final file = result.files.first;
    final bytes = file.bytes;
    if (bytes == null) return;
    if (!context.mounted) return;
    try {
      final ref = await api.uploadCabinetContent(
        cabinetId: cabinetId,
        filename: file.name,
        bytes: bytes,
        mime: _guessMime(file.name),
      );
      final maybeFuture = onChanged(ref);
      await maybeFuture;
    } catch (e) {
      if (context.mounted) {
        AppErrors.showSnack(context, e);
      }
    }
  }

  List<String>? _extensions() {
    final accept = this.accept;
    if (accept == null || accept.trim().isEmpty) return null;
    final parts = accept.split(',').map((e) => e.trim()).where((e) => e.isNotEmpty);
    final out = <String>[];
    for (final part in parts) {
      out.add(part.startsWith('.') ? part.substring(1) : part);
    }
    return out.isEmpty ? null : out;
  }

  String? _guessMime(String name) {
    final lower = name.toLowerCase();
    if (lower.endsWith('.md')) return 'text/markdown';
    if (lower.endsWith('.zip')) return 'application/zip';
    if (lower.endsWith('.json')) return 'application/json';
    if (lower.endsWith('.csv')) return 'text/csv';
    if (lower.endsWith('.xlsx')) {
      return 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
    }
    if (lower.endsWith('.xls')) return 'application/vnd.ms-excel';
    return 'application/octet-stream';
  }

  @override
  Widget build(BuildContext context) {
    final empty = !_hasFile;
    final warn = warnWhenEmpty && empty;
    final tokens = context.appColors;
    final accent = warn ? tokens.warning : null;
    final sub = (!empty && subtitle != null && subtitle!.trim().isNotEmpty)
        ? Text(subtitle!.trim())
        : null;

    return AppPreferenceTile(
      title: label,
      icon: Icons.upload_file_outlined,
      accentColor: accent,
      subtitle: sub,
      enabled: !readOnly,
      trailing: readOnly
          ? null
          : Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                if (_hasFile)
                  IconButton(
                    tooltip: 'Clear',
                    icon: const Icon(Icons.close),
                    visualDensity: VisualDensity.compact,
                    onPressed: () => onChanged(null),
                  ),
                const AppTrailingChevron(),
              ],
            ),
      onTap: readOnly ? null : () => _pick(context),
    );
  }
}

/// Pick .md file and return text content.
Future<String?> pickMarkdownFileText() async {
  final result = await FilePicker.platform.pickFiles(
    withData: true,
    type: FileType.custom,
    allowedExtensions: const ['md'],
  );
  if (result == null || result.files.isEmpty) return null;
  final bytes = result.files.first.bytes;
  if (bytes == null) return null;
  return utf8.decode(bytes);
}
