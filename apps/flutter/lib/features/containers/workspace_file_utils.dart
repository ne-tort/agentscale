export 'workspace_file_save_io.dart'
    if (dart.library.html) 'workspace_file_save_web.dart';

String formatWorkspaceFileSize(int? bytes) {
  if (bytes == null) return '-';
  if (bytes < 1024) return '$bytes B';
  if (bytes < 1024 * 1024) return '${(bytes / 1024).toStringAsFixed(1)} KB';
  if (bytes < 1024 * 1024 * 1024) {
    return '${(bytes / (1024 * 1024)).toStringAsFixed(1)} MB';
  }
  return '${(bytes / (1024 * 1024 * 1024)).toStringAsFixed(1)} GB';
}
