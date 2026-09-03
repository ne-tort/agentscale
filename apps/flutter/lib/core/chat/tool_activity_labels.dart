import 'package:prodavan/l10n/app_localizations.dart';

/// Normalized tool categories for grouping and labels.
enum ToolKind {
  fileRead,
  fileWrite,
  fileEdit,
  fileDelete,
  searchGlob,
  searchGrep,
  listDir,
  shell,
  mcp,
  subagent,
  generic,
}

ToolKind normalizeToolKind(String name) {
  final lower = name.toLowerCase().trim();
  if (lower.startsWith('mcp') || lower.contains('mcp__')) return ToolKind.mcp;
  switch (lower) {
    case 'read':
    case 'read_file':
      return ToolKind.fileRead;
    case 'write':
      return ToolKind.fileWrite;
    case 'edit':
    case 'strreplace':
    case 'str_replace':
      return ToolKind.fileEdit;
    case 'delete':
    case 'remove':
    case 'unlink':
      return ToolKind.fileDelete;
    case 'glob':
      return ToolKind.searchGlob;
    case 'grep':
    case 'ripgrep':
    case 'rg':
      return ToolKind.searchGrep;
    case 'ls':
    case 'list_dir':
    case 'listdir':
      return ToolKind.listDir;
    case 'shell':
    case 'bash':
    case 'run_terminal_cmd':
    case 'run_command':
      return ToolKind.shell;
    case 'task':
    case 'agent':
    case 'subagent':
      return ToolKind.subagent;
    default:
      return ToolKind.generic;
  }
}

String? _stringFrom(dynamic value) {
  if (value == null) return null;
  final s = value.toString().trim();
  return s.isEmpty ? null : s;
}

String? extractToolContextPath(Map<String, dynamic> input, [Object? output]) {
  for (final key in ['path', 'file_path', 'target_file', 'relative_path', 'target']) {
    final v = _stringFrom(input[key]);
    if (v != null) return v;
  }
  if (output is Map) {
    for (final key in ['path', 'file_path', 'target_file']) {
      final v = _stringFrom(output[key]);
      if (v != null) return v;
    }
  }
  return null;
}

String? extractToolContextPattern(Map<String, dynamic> input, [Object? output]) {
  for (final key in ['globPattern', 'glob_pattern', 'pattern', 'query', 'glob']) {
    final v = _stringFrom(input[key]);
    if (v != null) return v;
  }
  if (output is Map) {
    for (final key in ['globPattern', 'pattern', 'query']) {
      final v = _stringFrom(output[key]);
      if (v != null) return v;
    }
  }
  return null;
}

String? extractToolContextCommand(Map<String, dynamic> input) {
  for (final key in ['command', 'cmd']) {
    final v = _stringFrom(input[key]);
    if (v != null) return v;
  }
  return null;
}

String? summarizeToolOutput(Object? output) {
  if (output == null) return null;
  if (output is! Map) return null;
  final status = _stringFrom(output['status']);
  if (status != null) return status;
  final message = _stringFrom(output['message']);
  if (message != null) return message;
  return null;
}

({String label, String? detail}) formatToolActivityLabel(
  AppLocalizations l10n, {
  required String name,
  Map<String, dynamic> input = const {},
  Object? output,
  bool pending = false,
}) {
  final kind = normalizeToolKind(name);
  final path = extractToolContextPath(input, output);
  final pattern = extractToolContextPattern(input, output);
  final command = extractToolContextCommand(input);
  final outputSummary = summarizeToolOutput(output);

  String label;
  String? detail;
  switch (kind) {
    case ToolKind.fileRead:
      label = path != null ? l10n.projectChatToolReadPath(path) : l10n.projectChatToolReadFile;
    case ToolKind.fileWrite:
      label = path != null ? l10n.projectChatToolWritePath(path) : l10n.projectChatToolWriteFile;
    case ToolKind.fileEdit:
      label = path != null ? l10n.projectChatToolEditPath(path) : l10n.projectChatToolEditFile;
    case ToolKind.fileDelete:
      label = path != null ? l10n.projectChatToolDeletePath(path) : l10n.projectChatToolDeleteFile;
    case ToolKind.searchGlob:
      label = pattern != null ? l10n.projectChatToolGlobPattern(pattern) : l10n.projectChatToolGlob;
    case ToolKind.searchGrep:
      label = pattern != null ? l10n.projectChatToolGrepPattern(pattern) : l10n.projectChatToolGrep;
    case ToolKind.listDir:
      label = path != null ? l10n.projectChatToolListDirPath(path) : l10n.projectChatToolListDir;
    case ToolKind.shell:
      label = l10n.projectChatToolShell;
      detail = command;
    case ToolKind.mcp:
      final tool = name.replaceFirst(RegExp(r'^mcp[_-]*', caseSensitive: false), '').trim();
      label = tool.isNotEmpty ? l10n.projectChatToolMcp(tool) : l10n.projectChatToolMcpGeneric;
    case ToolKind.subagent:
      label = l10n.projectChatToolSubagent(name);
    case ToolKind.generic:
      label = l10n.projectChatToolGeneric(name);
      detail = outputSummary ?? command ?? pattern ?? path;
  }

  if (detail == null && outputSummary != null && kind != ToolKind.shell && kind != ToolKind.generic) {
    detail = outputSummary;
  }

  if (pending) {
    label = '$label…';
  }
  return (label: label, detail: detail);
}

/// Backward-compatible helper for grouping heuristics without l10n.
ToolKind toolKindFromPair(String name, Map<String, dynamic> input) => normalizeToolKind(name);
