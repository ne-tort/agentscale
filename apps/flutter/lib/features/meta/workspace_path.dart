/// Soft-normalize a workspace-relative path for display/persist (no OS root).
/// Strips leading slashes, unifies `\`, rejects `..` by returning empty.
String normalizeWorkspaceRelativePath(String raw) {
  var text = raw.trim().replaceAll('\\', '/');
  if (text.isEmpty || text == '/' || text == '.') return '';
  while (text.startsWith('/')) {
    text = text.substring(1);
  }
  final parts = text.split('/').where((p) => p.isNotEmpty && p != '.').toList();
  if (parts.any((p) => p == '..')) return '';
  return parts.join('/');
}

/// Empty path → em dash for collection cells.
String formatPathCell(dynamic value) {
  if (value == null) return '—';
  final text = value.toString().trim();
  if (text.isEmpty) return '—';
  final normalized = normalizeWorkspaceRelativePath(text);
  return normalized.isEmpty ? '—' : normalized;
}
