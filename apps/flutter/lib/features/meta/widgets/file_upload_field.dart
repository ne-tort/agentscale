import 'dart:convert';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/theme/app_spacing.dart';

/// Upload file via cabinet content API; stores FileRef map in form state.
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
  });

  final String label;
  final dynamic value;
  final String cabinetId;
  final ProdavanApi api;
  final ValueChanged<Map<String, dynamic>?> onChanged;
  final bool readOnly;
  final String? accept;

  Map<String, dynamic>? get _ref =>
      value is Map ? Map<String, dynamic>.from(value as Map) : null;

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
      onChanged(ref);
    } catch (e) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('$e')),
        );
      }
    }
  }

  List<String>? _extensions() {
    final accept = this.accept;
    if (accept == null) return null;
    if (accept.startsWith('.')) {
      return [accept.substring(1)];
    }
    return null;
  }

  String? _guessMime(String name) {
    if (name.endsWith('.md')) return 'text/markdown';
    if (name.endsWith('.zip')) return 'application/zip';
    if (name.endsWith('.json')) return 'application/json';
    return 'application/octet-stream';
  }

  @override
  Widget build(BuildContext context) {
    final ref = _ref;
    final filename = ref?['filename'] as String? ?? '—';
    return Padding(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.md,
        vertical: AppSpacing.sm,
      ),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(label, style: Theme.of(context).textTheme.titleSmall),
                const SizedBox(height: AppSpacing.xs),
                Text(filename, style: Theme.of(context).textTheme.bodyMedium),
              ],
            ),
          ),
          if (!readOnly)
            IconButton(
              tooltip: 'Upload',
              icon: const Icon(Icons.upload_file_outlined),
              onPressed: () => _pick(context),
            ),
          if (!readOnly && ref != null)
            IconButton(
              tooltip: 'Clear',
              icon: const Icon(Icons.close),
              onPressed: () => onChanged(null),
            ),
        ],
      ),
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
