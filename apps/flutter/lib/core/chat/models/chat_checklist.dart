/// Checklist (todo) state derived from the chat transcript.
///
/// The agent maintains a task list through the runtime `todo.write` tool
/// (and, for vendor SDKs, a `task_progress` event projected to a `plan`
/// block). Both are persisted in the transcript, so the checklist survives a
/// page reload without extra server state — this module folds those blocks
/// into the current task list the UI renders.
///
/// Pure functions only: no widgets, no I/O — unit-testable in isolation.
library;

import 'dart:convert';

import 'chat_block.dart';

/// Semantic colors a task may carry (used sparingly, only when meaningful).
enum ChatTaskColor { success, warning, error, info }

/// One checklist task with normalized status, optional color and comment.
class ChatChecklistTask {
  const ChatChecklistTask({
    required this.id,
    required this.title,
    required this.status,
    this.color,
    this.comment,
  });

  final String id;
  final String title;

  /// One of `pending` / `in_progress` / `completed` / `blocked` /
  /// `deferred` / `partial` (unknown → `pending`).
  final String status;

  /// Optional semantic color (drives the leading icon in the UI).
  final ChatTaskColor? color;

  /// Optional short note, shown collapsed until the user expands the task.
  final String? comment;

  bool get isCompleted => status == 'completed' || status == 'done';
  bool get isInProgress => status == 'in_progress' || status == 'running' || status == 'active';
  bool get isBlocked => status == 'blocked';
  bool get isDeferred => status == 'deferred';
  bool get isPartial => status == 'partial';
  bool get hasComment => comment != null && comment!.trim().isNotEmpty;

  Map<String, dynamic> toJson() => {
        'id': id,
        'title': title,
        'status': status,
        if (color != null) 'color': color!.name,
        if (comment != null) 'comment': comment,
      };
}

/// Strip the `mcp.openclaw.` prefix so a tool name compares canonically.
String canonicalToolName(String name) {
  final trimmed = name.trim();
  const prefix = 'mcp.openclaw.';
  if (trimmed.toLowerCase().startsWith(prefix)) {
    return trimmed.substring(prefix.length);
  }
  return trimmed;
}

/// Normalize any status word the model used to a canonical state.
String _normalizeStatus(Object? raw) {
  final s = raw?.toString().trim().toLowerCase();
  switch (s) {
    case 'completed':
    case 'complete':
    case 'done':
    case 'ok':
    case 'closed':
      return 'completed';
    case 'in_progress':
    case 'in-progress':
    case 'inprogress':
    case 'running':
    case 'active':
    case 'doing':
    case 'started':
      return 'in_progress';
    case 'blocked':
    case 'stuck':
      return 'blocked';
    case 'deferred':
    case 'postponed':
    case 'later':
    case 'waiting':
      return 'deferred';
    case 'partial':
    case 'partially':
    case 'partially_done':
    case 'partially-done':
      return 'partial';
    default:
      return 'pending';
  }
}

ChatTaskColor? _normalizeColor(Object? raw) {
  final s = raw?.toString().trim().toLowerCase().replaceAll(RegExp(r'^<+'), '').replaceAll(RegExp(r'>+$'), '');
  switch (s) {
    case 'success':
    case 'ok':
    case 'good':
    case 'green':
      return ChatTaskColor.success;
    case 'warning':
    case 'warn':
    case 'attention':
    case 'yellow':
      return ChatTaskColor.warning;
    case 'error':
    case 'fail':
    case 'failed':
    case 'danger':
    case 'problem':
    case 'red':
      return ChatTaskColor.error;
    case 'info':
    case 'note':
    case 'blue':
      return ChatTaskColor.info;
    default:
      return null;
  }
}

// A `<tag>` may carry a color (`<success>`) or a status hint (`<blocked>`),
// recognized anywhere in the title (see [_parseContent]).

const _tagStatusHints = <String, String>{
  'blocked': 'blocked',
  'deferred': 'deferred',
  'postponed': 'deferred',
  'partial': 'partial',
  'pending': 'pending',
  'done': 'completed',
  'completed': 'completed',
};

class _ParsedContent {
  const _ParsedContent(this.title, this.color, this.statusHint);
  final String title;
  final ChatTaskColor? color;
  final String? statusHint;
}

// A `<tag>` may carry a color (`<success>`) or a status hint (`<blocked>`).
// Recognized tags are stripped from the title ANYWHERE in the text (the model
// does not always put them first), so they never leak into the UI. Unknown
// tags are left untouched (they might be legitimate text).
final _anyTag = RegExp(r'<\s*([a-zA-Z_]+)\s*>');

/// Remove recognized `<tag>`s from the title, collecting their meaning.
_ParsedContent _parseContent(String raw) {
  ChatTaskColor? color;
  String? statusHint;
  final cleaned = raw.replaceAllMapped(_anyTag, (m) {
    final tag = m.group(1)!.toLowerCase();
    final hinted = _tagStatusHints[tag];
    if (hinted != null) {
      statusHint ??= hinted;
      return '';
    }
    final c = _normalizeColor(tag);
    if (c != null) {
      color ??= c;
      return '';
    }
    return m.group(0)!; // unknown tag → keep verbatim
  });
  // Tidy leftovers: doubled spaces and a dangling leading separator/punct.
  final title = cleaned
      .replaceAll(RegExp(r'\s{2,}'), ' ')
      .replaceAll(RegExp(r'^\s*[:;,\-–—]+\s*'), '')
      .trim();
  return _ParsedContent(title, color, statusHint);
}

ChatChecklistTask? _taskFromMap(Map map, {int fallbackIndex = 0}) {
  final rawTitle = (map['content'] ?? map['title'])?.toString().trim();
  final parsed = _parseContent(rawTitle ?? '');
  // Никогда не показываем id как заголовок: без текста задачи строка
  // бессмысленна (id «todo-1» — это артефакт рантайма, не название).
  final title = parsed.title;
  if (title.isEmpty) return null;
  final id = (map['id'] ?? map['task_id'])?.toString();
  final hasStatus = map['status'] != null && map['status'].toString().trim().isNotEmpty;
  final status = hasStatus ? _normalizeStatus(map['status']) : (parsed.statusHint ?? 'pending');
  final comment = map['comment']?.toString().trim();
  return ChatChecklistTask(
    id: (id == null || id.isEmpty) ? 'task-$fallbackIndex' : id,
    title: title,
    status: status,
    color: _normalizeColor(map['color']) ?? parsed.color,
    comment: (comment == null || comment.isEmpty) ? null : comment,
  );
}

/// Fold the transcript into the current checklist task list.
///
/// Replays, in order: `todo.write` tool RESULTS (the authoritative snapshot
/// with runtime-assigned ids — the agent's later `items`/`patch` calls carry
/// `id: null` / `content: null`, which without the result snapshot lost task
/// titles and showed the id «todo-1» as the title), then `plan` blocks and
/// `todo.write` CALLS as fallback for transcripts that lack the result blocks.
/// The latest state wins; an explicit empty list clears it.
List<ChatChecklistTask> deriveChecklistTasks(List<ChatBlock> blocks) {
  // id → task, insertion-ordered so the list keeps the model's ordering.
  final Map<String, ChatChecklistTask> state = {};

  for (final block in blocks) {
    // todo.write RESULT: полный снимок с назначенными рантаймом id — главный
    // источник истины (входные items/patch их не всегда несут).
    if (block.kind == 'tool_result') {
      final name = canonicalToolName(block.raw['name']?.toString() ?? '');
      if (name != 'todo.write') continue;
      final todos = _todosFromResult(block.raw);
      if (todos == null) continue;
      final next = <String, ChatChecklistTask>{};
      for (var i = 0; i < todos.length; i++) {
        final task = _taskFromMap(todos[i], fallbackIndex: i);
        if (task != null) next[task.id] = task;
      }
      state
        ..clear()
        ..addAll(next);
      continue;
    }

    if (block.kind == 'plan') {
      final raw = block.raw['tasks'];
      if (raw is! List) continue;
      final next = <String, ChatChecklistTask>{};
      for (var i = 0; i < raw.length; i++) {
        final item = raw[i];
        if (item is! Map) continue;
        final task = _taskFromMap(item, fallbackIndex: i);
        if (task != null) next[task.id] = task;
      }
      // A plan block is authoritative (full snapshot), even if empty.
      state
        ..clear()
        ..addAll(next);
      continue;
    }

    if (block.kind != 'tool_call') continue;
    final name = canonicalToolName(block.raw['name']?.toString() ?? '');
    if (name != 'todo.write') continue;
    final input = block.raw['input'];
    if (input is! Map) continue;

    final items = input['items'];
    if (items is List) {
      final next = <String, ChatChecklistTask>{};
      for (var i = 0; i < items.length; i++) {
        final item = items[i];
        if (item is! Map) continue;
        final task = _taskFromMap(item, fallbackIndex: i);
        if (task != null) next[task.id] = task;
      }
      state
        ..clear()
        ..addAll(next);
    }

    final patch = input['patch'];
    if (patch is List) {
      for (final item in patch) {
        if (item is! Map) continue;
        final id = (item['id'] ?? item['task_id'])?.toString();
        if (id == null || id.isEmpty) continue;
        final prev = state[id];
        // Merge onto the previous task so an omitted field is preserved.
        final merged = <String, dynamic>{
          'id': id,
          'content': item['content'] ?? item['title'] ?? prev?.title,
          'status': item.containsKey('status') ? item['status'] : prev?.status,
          'color': item.containsKey('color') ? item['color'] : prev?.color?.name,
          'comment': item.containsKey('comment') ? item['comment'] : prev?.comment,
        };
        final task = _taskFromMap(merged);
        if (task != null) state[id] = task;
      }
    }
  }

  return state.values.toList(growable: false);
}

/// How many of the tasks are done (for the compact "x/y" header).
int checklistCompletedCount(List<ChatChecklistTask> tasks) =>
    tasks.where((t) => t.isCompleted).length;

/// Extract the authoritative `todos` list from a `todo.write` tool RESULT.
///
/// The runtime returns `{"todos": [...]}` as a JSON string in
/// `output.content[0].text`; those entries carry the runtime-assigned ids AND
/// content, which the agent's `patch` calls omit. Returns null when the block
/// holds no todo list.
List<Map>? _todosFromResult(Map<String, dynamic> raw) {
  final output = raw['output'];
  String? text;
  if (output is Map) {
    final content = output['content'];
    if (content is List && content.isNotEmpty) {
      final first = content.first;
      if (first is Map) text = first['text']?.toString();
    }
  } else if (output is String) {
    text = output;
  }
  if (text == null || text.isEmpty) return null;
  try {
    final decoded = jsonDecode(text);
    if (decoded is Map && decoded['todos'] is List) {
      return [for (final t in decoded['todos'] as List) if (t is Map) t];
    }
  } on FormatException {
    return null;
  }
  return null;
}
