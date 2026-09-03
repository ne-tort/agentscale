import 'dart:convert';

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

Map<String, dynamic> _asStringKeyedMap(Object? value) {
  if (value is Map<String, dynamic>) return value;
  if (value is Map) {
    return value.map((k, v) => MapEntry(k.toString(), v));
  }
  return const {};
}

/// Flatten Cursor SDK wrappers: `{status, value}`, `{success: …}`, `{error: …}`.
Object? unwrapToolPayload(Object? output) {
  if (output == null) return null;
  if (output is! Map) return output;
  final map = _asStringKeyedMap(output);

  if (map.containsKey('value')) return unwrapToolPayload(map['value']);
  if (map.containsKey('success')) return unwrapToolPayload(map['success']);
  if (map.containsKey('error')) return unwrapToolPayload(map['error']);
  if (map.containsKey('rejected')) return unwrapToolPayload(map['rejected']);

  // Nested args-style envelope.
  if (map.length == 1 && map.containsKey('result')) {
    return unwrapToolPayload(map['result']);
  }
  return map;
}

Map<String, dynamic> normalizeToolInput(Map<String, dynamic> input) {
  if (input.containsKey('args') && input['args'] is Map) {
    return {...input, ..._asStringKeyedMap(input['args'])};
  }
  return input;
}

String? extractToolContextPath(Map<String, dynamic> input, [Object? output]) {
  final inMap = normalizeToolInput(input);
  for (final key in ['path', 'file_path', 'target_file', 'relative_path', 'target']) {
    final v = _stringFrom(inMap[key]);
    if (v != null) return v;
  }
  final unwrapped = unwrapToolPayload(output);
  if (unwrapped is Map) {
    final out = _asStringKeyedMap(unwrapped);
    for (final key in ['path', 'file_path', 'target_file']) {
      final v = _stringFrom(out[key]);
      if (v != null) return v;
    }
  }
  return null;
}

String? extractToolContextPattern(Map<String, dynamic> input, [Object? output]) {
  final inMap = normalizeToolInput(input);
  for (final key in ['globPattern', 'glob_pattern', 'pattern', 'query', 'glob']) {
    final v = _stringFrom(inMap[key]);
    if (v != null) return v;
  }
  final unwrapped = unwrapToolPayload(output);
  if (unwrapped is Map) {
    final out = _asStringKeyedMap(unwrapped);
    for (final key in ['globPattern', 'pattern', 'query']) {
      final v = _stringFrom(out[key]);
      if (v != null) return v;
    }
  }
  return null;
}

String? extractToolContextCommand(Map<String, dynamic> input) {
  final inMap = normalizeToolInput(input);
  for (final key in ['command', 'cmd']) {
    final v = _stringFrom(inMap[key]);
    if (v != null) return v;
  }
  return null;
}

String _prettyJson(Object? value) {
  try {
    return const JsonEncoder.withIndent('  ').convert(value);
  } catch (_) {
    return value.toString();
  }
}

String? _listPathsFrom(Object? value) {
  if (value is List) {
    final paths = value.map(_stringFrom).whereType<String>().toList();
    if (paths.isEmpty) return null;
    return paths.join('\n');
  }
  if (value is Map) {
    final map = _asStringKeyedMap(value);
    for (final key in ['files', 'paths', 'matches', 'results']) {
      final nested = _listPathsFrom(map[key]);
      if (nested != null) return nested;
    }
  }
  return null;
}

({int added, int removed})? parseDiffStats(Object? output) {
  if (output == null) return null;
  final unwrapped = unwrapToolPayload(output);
  if (unwrapped is Map) {
    final add = unwrapped['lines_added'] ?? unwrapped['added_lines'] ?? unwrapped['additions'];
    final rem = unwrapped['lines_removed'] ?? unwrapped['removed_lines'] ?? unwrapped['deletions'];
    if (add is num || rem is num) {
      return (added: (add as num?)?.toInt() ?? 0, removed: (rem as num?)?.toInt() ?? 0);
    }
  }
  final text = (unwrapped ?? output).toString();
  if (text.isEmpty) return null;
  var added = 0;
  var removed = 0;
  for (final line in text.split('\n')) {
    if (line.startsWith('+++') || line.startsWith('---') || line.startsWith('@@')) continue;
    if (line.startsWith('+')) added++;
    if (line.startsWith('-')) removed++;
  }
  if (added == 0 && removed == 0) return null;
  return (added: added, removed: removed);
}

/// Human-readable panel body for tool expand (not raw Map.toString()).
String formatToolPanelBody({
  required ToolKind kind,
  Map<String, dynamic> input = const {},
  Object? output,
}) {
  final inMap = normalizeToolInput(input);
  final unwrapped = unwrapToolPayload(output);

  switch (kind) {
    case ToolKind.fileDelete:
      final path = extractToolContextPath(inMap, output);
      if (unwrapped is Map) {
        final size = unwrapped['fileSize'] ?? unwrapped['file_size'];
        if (size != null && path != null) return '$path\nfileSize: $size';
        if (size != null) return 'fileSize: $size';
      }
      if (path != null) return path;
      return unwrapped == null ? '' : _prettyJson(unwrapped);
    case ToolKind.fileRead:
      if (unwrapped is Map) {
        final content = unwrapped['content'] ?? unwrapped['text'];
        if (content != null) return content.toString();
      }
      if (unwrapped is String) return unwrapped;
      return unwrapped == null ? '' : _prettyJson(unwrapped);
    case ToolKind.fileWrite:
    case ToolKind.fileEdit:
      final path = extractToolContextPath(inMap, output);
      final stats = parseDiffStats(output);
      final buf = StringBuffer();
      if (path != null) buf.writeln(path);
      if (stats != null && (stats.added > 0 || stats.removed > 0)) {
        buf.writeln('+${stats.added} −${stats.removed}');
      }
      if (unwrapped is Map) {
        final content = unwrapped['content'] ?? unwrapped['diff'] ?? unwrapped['patch'];
        if (content != null && content.toString().trim().isNotEmpty) {
          buf.writeln(content.toString().trimRight());
        }
      } else if (unwrapped is String && unwrapped.trim().isNotEmpty) {
        buf.writeln(unwrapped.trimRight());
      }
      final text = buf.toString().trim();
      if (text.isNotEmpty) return text;
      return unwrapped == null ? (path ?? '') : _prettyJson(unwrapped);
    case ToolKind.searchGlob:
      final paths = _listPathsFrom(unwrapped);
      if (paths != null) return paths;
      if (unwrapped is Map) {
        final total = unwrapped['totalFiles'] ?? unwrapped['total_files'] ?? unwrapped['count'];
        if (total != null) return '$total files';
      }
      return unwrapped == null ? '' : _prettyJson(unwrapped);
    case ToolKind.searchGrep:
      if (unwrapped is Map) {
        final workspace = unwrapped['workspaceResults'] ?? unwrapped['workspace_results'];
        if (workspace is Map) {
          final buf = StringBuffer();
          for (final entry in workspace.entries) {
            buf.writeln(entry.key);
            final content = entry.value;
            if (content is Map) {
              final inner = content['content'] ?? content;
              if (inner is Map) {
                final lines = inner['matchedLines'] ??
                    inner['matched_lines'] ??
                    inner['totalMatchedLines'] ??
                    inner['total_matched_lines'];
                if (lines is List) {
                  for (final line in lines) {
                    buf.writeln('  $line');
                  }
                } else if (lines != null) {
                  buf.writeln('  matches: $lines');
                }
              } else {
                buf.writeln('  $inner');
              }
            } else {
              buf.writeln('  $content');
            }
          }
          final text = buf.toString().trim();
          if (text.isNotEmpty) return text;
        }
        final paths = _listPathsFrom(unwrapped);
        if (paths != null) return paths;
      }
      return unwrapped == null ? '' : _prettyJson(unwrapped);
    case ToolKind.listDir:
      final paths = _listPathsFrom(unwrapped);
      if (paths != null) return paths;
      return unwrapped == null ? '' : _prettyJson(unwrapped);
    case ToolKind.shell:
      final cmd = extractToolContextCommand(inMap);
      final buf = StringBuffer();
      if (cmd != null) buf.writeln('\$ $cmd');
      if (unwrapped is Map) {
        final code = unwrapped['exitCode'] ?? unwrapped['exit_code'];
        if (code != null) buf.writeln('exit $code');
        final stdout = unwrapped['stdout'] ?? unwrapped['output'];
        final stderr = unwrapped['stderr'];
        if (stdout != null && stdout.toString().trim().isNotEmpty) {
          buf.writeln(stdout.toString().trimRight());
        }
        if (stderr != null && stderr.toString().trim().isNotEmpty) {
          buf.writeln(stderr.toString().trimRight());
        }
      } else if (unwrapped is String) {
        buf.writeln(unwrapped);
      }
      return buf.toString().trim();
    case ToolKind.mcp:
    case ToolKind.subagent:
    case ToolKind.generic:
      if (unwrapped == null) return '';
      if (unwrapped is String) return unwrapped;
      return _prettyJson(unwrapped);
  }
}

({String label, String? detail}) formatToolActivityLabel(
  AppLocalizations l10n, {
  required String name,
  Map<String, dynamic> input = const {},
  Object? output,
  bool pending = false,
}) {
  final kind = normalizeToolKind(name);
  final inMap = normalizeToolInput(input);
  final path = extractToolContextPath(inMap, output);
  final pattern = extractToolContextPattern(inMap, output);
  final command = extractToolContextCommand(inMap);

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
      detail = command ?? pattern ?? path;
  }

  if (pending) {
    label = '$label…';
  }
  return (label: label, detail: detail);
}

/// Backward-compatible helper for grouping heuristics without l10n.
ToolKind toolKindFromPair(String name, Map<String, dynamic> input) => normalizeToolKind(name);
