import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';

String formatWorkspaceFileSize(int? bytes) {
  if (bytes == null) return '—';
  if (bytes < 1024) return '$bytes B';
  if (bytes < 1024 * 1024) return '${(bytes / 1024).toStringAsFixed(1)} KB';
  if (bytes < 1024 * 1024 * 1024) {
    return '${(bytes / (1024 * 1024)).toStringAsFixed(1)} MB';
  }
  return '${(bytes / (1024 * 1024 * 1024)).toStringAsFixed(1)} GB';
}

Future<bool> saveWorkspaceFileBytes({
  required String filename,
  required Uint8List bytes,
}) async {
  final path = await FilePicker.platform.saveFile(
    fileName: filename,
    bytes: bytes,
  );
  return path != null;
}
