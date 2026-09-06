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
