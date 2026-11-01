import 'dart:async';
import 'dart:convert';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/app_preference_tile.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_snack_bar.dart';
import 'package:prodavan/core/widgets/app_trailing_chevron.dart';
import 'package:prodavan/features/containers/workspace_file_utils.dart';
import 'package:prodavan/features/meta/runtime/module_runtime_scope.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Upload file via owner-scoped content API; stores FileRef map in form state.
///
/// Preference-tile chrome (same row pattern as [AppNavPreference]): tap to pick.
/// When [warnWhenEmpty] and no file — title uses warning color and [subtitle] is omitted.
class FileUploadField extends StatefulWidget {
  const FileUploadField({
    super.key,
    required this.label,
    required this.value,
    required this.scope,
    required this.onChanged,
    this.readOnly = false,
    this.accept,
    this.subtitle,
    this.warnWhenEmpty = false,
  });

  final String label;
  final dynamic value;
  final ModuleRuntimeScope scope;
  final FutureOr<void> Function(Map<String, dynamic>?) onChanged;
  final bool readOnly;
  final String? accept;
  /// Shown only when a file is present and subtitle is non-empty.
  final String? subtitle;
  final bool warnWhenEmpty;

  @override
  State<FileUploadField> createState() => _FileUploadFieldState();
}

class _FileUploadFieldState extends State<FileUploadField> {
  bool _downloading = false;

  Map<String, dynamic>? get _ref =>
      widget.value is Map ? Map<String, dynamic>.from(widget.value as Map) : null;

  bool get _hasFile => _ref != null;

  List<String>? _extensions() {
    final accept = widget.accept;
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

  Future<void> _pick(BuildContext context) async {
    final result = await FilePicker.platform.pickFiles(
      withData: true,
      type: FileType.custom,
      allowedExtensions: widget._extensions(),
    );
    if (result == null || result.files.isEmpty) return;
    final file = result.files.first;
    final bytes = file.bytes;
    if (bytes == null) return;
    if (!context.mounted) return;
    try {
      final ref = await widget.scope.uploadContent(
        filename: file.name,
        bytes: bytes,
        mime: widget._guessMime(file.name),
      );
      final maybeFuture = widget.onChanged(ref);
      await maybeFuture;
    } catch (e) {
      if (context.mounted) {
        AppErrors.showSnack(context, e);
      }
    }
  }

  Future<void> _download(BuildContext context) async {
    final scope = widget.scope;
    final ref = _ref;
    final assetId = ref?['asset_id']?.toString() ?? '';
    if (assetId.isEmpty || scope.downloadContentFn == null || _downloading) return;
    setState(() => _downloading = true);
    try {
      final bytes = await scope.downloadContentAsset(
        assetId: assetId,
        versionId: ref?['version_id']?.toString(),
        blobVersionId: ref?['blob_version_id']?.toString(),
      );
      var filename = ref?['filename']?.toString().trim() ?? '';
      if (filename.isEmpty) filename = 'download';
      final saved = await saveWorkspaceFileBytes(filename: filename, bytes: bytes);
      if (saved && context.mounted) {
        AppSnackBar.success(
          context,
          AppLocalizations.of(context).projectWorkspaceDownloaded,
        );
      }
    } catch (e) {
      if (context.mounted) AppErrors.showSnack(context, e);
    } finally {
      if (mounted) setState(() => _downloading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final empty = !_hasFile;
    final warn = widget.warnWhenEmpty && empty;
    final tokens = context.appColors;
    final accent = warn ? tokens.warning : null;
    String? effectiveSubtitle = widget.subtitle;
    if (!empty && (effectiveSubtitle == null || effectiveSubtitle.trim().isEmpty)) {
      final name = _ref?['filename']?.toString() ?? '';
      if (name.isNotEmpty) effectiveSubtitle = name;
    }
    final sub = (!empty && effectiveSubtitle != null && effectiveSubtitle.trim().isNotEmpty)
        ? Text(effectiveSubtitle.trim())
        : null;

    return AppPreferenceTile(
      title: widget.label,
      icon: Icons.upload_file_outlined,
      accentColor: accent,
      subtitle: sub,
      enabled: !widget.readOnly || _hasFile,
      trailing: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (_hasFile && widget.scope.downloadContentFn != null)
            IconButton(
              tooltip: 'Скачать файл',
              icon: _downloading
                  ? const SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Icon(Icons.download_outlined),
              visualDensity: VisualDensity.compact,
              onPressed: _downloading ? null : () => _download(context),
            ),
          if (_hasFile && !widget.readOnly)
            IconButton(
              tooltip: 'Clear',
              icon: const Icon(Icons.close),
              visualDensity: VisualDensity.compact,
              onPressed: () => widget.onChanged(null),
            ),
          if (!widget.readOnly) const AppTrailingChevron(),
        ],
      ),
      onTap: widget.readOnly ? null : () => _pick(context),
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
