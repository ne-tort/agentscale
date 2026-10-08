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

import 'chat_block.dart';

/// One checklist task with normalized status.
class ChatChecklistTask {
  const ChatChecklistTask({
    required this.id,
    required this.title,
    required this.status,
  });

  final String id;
  final String title;

  /// One of `pending` / `in_progress` / `completed` (unknown → `pending`).
  final String status;

  bool get isCompleted => status == 'completed' || status == 'done';
  bool get isInProgress => status == 'in_progress' || status == 'running';

  Map<String, dynamic> toJson() => {'id': id, 'title': title, 'status': status};
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

String _normalizeStatus(Object? raw) {
  final s = raw?.toString().trim().toLowerCase();
  switch (s) {
    case 'completed':
    case 'done':
      return 'completed';
    case 'in_progress':
    case 'in-progress':
    case 'running':
    case 'active':
      return 'in_progress';
    default:
      return 'pending';
  }
}

ChatChecklistTask? _taskFromMap(Map map, {int fallbackIndex = 0}) {
  final title = (map['content'] ?? map['title'] ?? map['id'])?.toString().trim();
  if (title == null || title.isEmpty) return null;
  final id = (map['id'] ?? map['task_id'])?.toString();
  return ChatChecklistTask(
    id: (id == null || id.isEmpty) ? 'task-$fallbackIndex' : id,
    title: title,
    status: _normalizeStatus(map['status']),
  );
}

/// Fold the transcript into the current checklist task list.
///
/// Replays, in order: `plan` blocks (vendor task events, full replacement)
/// and `todo.write` tool calls (`items` = full replacement, `patch` = update
/// by id). The latest state wins; an explicit empty `items` clears the list.
/// Returns an empty list when the agent never produced a checklist.
List<ChatChecklistTask> deriveChecklistTasks(List<ChatBlock> blocks) {
  // id → task, insertion-ordered so the list keeps the model's ordering.
  final Map<String, ChatChecklistTask> state = {};

  for (final block in blocks) {
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
        final title = (item['content'] ?? item['title'] ?? prev?.title)?.toString().trim();
        if (title == null || title.isEmpty) continue;
        state[id] = ChatChecklistTask(
          id: id,
          title: title,
          status: item.containsKey('status')
              ? _normalizeStatus(item['status'])
              : (prev?.status ?? 'pending'),
        );
      }
    }
  }

  return state.values.toList(growable: false);
}

/// How many of the tasks are done (for the compact "x/y" header).
int checklistCompletedCount(List<ChatChecklistTask> tasks) =>
    tasks.where((t) => t.isCompleted).length;
