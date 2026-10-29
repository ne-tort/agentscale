import 'package:prodavan/core/chat/models/chat_block.dart';
import 'package:prodavan/core/chat/tool_activity_labels.dart';

/// Final-answer tags — the runtime is instructed (see the API
/// `answer_protocol` module) to wrap the user-facing answer in these.
const String chatFinalAnswerOpen = '<final_answer>';
const String chatFinalAnswerClose = '</final_answer>';

/// One conversational turn: the user prompt, ALL agent work collapsed into a
/// single expandable group, and exactly one final answer.
///
/// This is the whole chat model. The UI renders one [_ChatTurn] per item, so
/// block order, collapse state and reload behaviour are stable by
/// construction — there is no per-block "is this the final answer?" heuristic
/// at render time.
class ChatTurn {
  const ChatTurn({
    required this.id,
    required this.user,
    required this.work,
    required this.finalText,
    required this.notices,
    required this.running,
    required this.actionCount,
    required this.durationMs,
    required this.startedAt,
    required this.diffStats,
    required this.hasPendingApproval,
  });

  /// Stable across transcript reloads: user-block timestamp + text hash.
  final String id;

  final ChatBlock? user;

  /// Everything the agent did in this turn, in order. Never rendered unless
  /// the user expands the turn.
  final List<ChatBlock> work;

  /// Final answer markdown, tags stripped. Empty when the turn has none yet.
  final String finalText;

  /// Errors / system notices — visible outside the collapsed work block.
  final List<ChatBlock> notices;

  final bool running;
  final int actionCount;
  final int? durationMs;
  final DateTime? startedAt;
  final ({int added, int removed})? diffStats;

  /// An approval in this turn is still waiting for the user. Resolved
  /// approvals stay in the work log but must not keep the turn expanded.
  final bool hasPendingApproval;

  bool get hasFinal => finalText.trim().isNotEmpty;

  bool get isEmpty =>
      user == null && work.isEmpty && !hasFinal && notices.isEmpty;
}

/// Build the turn timeline from a flat block list (transcript or live SSE).
///
/// Deterministic: the same blocks always produce the same turns, which is what
/// makes a page reload mid-turn look identical to the live feed.
List<ChatTurn> buildChatTurns(
  List<ChatBlock> blocks, {
  bool turnInProgress = false,
  DateTime? now,
  Set<String> pendingApprovalIds = const {},
}) {
  final turns = <ChatTurn>[];
  var draft = _TurnDraft();
  var index = 0;

  void flush({bool last = false}) {
    if (draft.isEmpty) {
      draft = _TurnDraft();
      return;
    }
    turns.add(
      draft.build(
        index: index,
        running: last && turnInProgress,
        now: now,
        pendingApprovalIds: pendingApprovalIds,
      ),
    );
    index++;
    draft = _TurnDraft();
  }

  for (final block in blocks) {
    if (block.kind == 'user') {
      flush();
      draft.user = block;
      continue;
    }
    draft.add(block);
  }
  flush(last: true);
  return turns;
}

class _TurnDraft {
  ChatBlock? user;
  final List<ChatBlock> _work = [];
  final List<ChatBlock> _notices = [];
  final _FinalAnswerScanner _scanner = _FinalAnswerScanner();

  String _answer = '';
  DateTime? _answerAt;
  bool get isEmpty =>
      user == null && _work.isEmpty && _notices.isEmpty && _answer.isEmpty;

  void add(ChatBlock block) {
    switch (block.kind) {
      case 'assistant_markdown':
        final split = _scanner.feed(block.text);
        if (split.work.trim().isNotEmpty) {
          _work.add(block.copyWithRaw({'text': split.work}));
        }
        if (split.answer.isNotEmpty) {
          _answer += split.answer;
          _answerAt = _time(block.raw['created_at']) ?? _answerAt;
        }
      case 'error':
      case 'system_notice':
      case 'permission_denial':
        _notices.add(block);
      case 'usage':
      case 'status':
      case 'tool_progress':
        // Token metadata / reconnect / progress noise: the header and
        // reconnect indicator already cover it; a usage-only turn must not
        // grow a fake "worked" row.
        break;
      default:
        _work.add(block);
    }
  }

  ChatTurn build({
    required int index,
    required bool running,
    DateTime? now,
    Set<String> pendingApprovalIds = const {},
  }) {
    // No explicit tag (older runtime / non-compliant model): the last
    // assistant message is the answer. While the turn is STILL RUNNING only
    // the chronological tail can be the answer — otherwise a preamble written
    // before a tool call would flash as the final answer and collapse again
    // when the real answer starts streaming. A finished turn may take its last
    // assistant message even if a trailing tool call follows it.
    if (_answer.trim().isEmpty && !_scanner.sawTag) {
      final candidate = running
          ? (_work.isNotEmpty && _work.last.kind == 'assistant_markdown'
                ? _work.last
                : null)
          : _lastAssistant(_work);
      if (candidate != null && candidate.text.trim().isNotEmpty) {
        _answer = candidate.text;
        _answerAt = _time(candidate.raw['created_at']);
        _work.remove(candidate);
      }
    }

    final startedAt = _time(user?.raw['created_at']) ?? _firstWorkTime();
    final endedAt = running
        ? now
        : (_answerAt ?? _lastWorkTime() ?? _time(user?.raw['created_at']));
    int? durationMs;
    if (startedAt != null && endedAt != null) {
      final ms = endedAt.difference(startedAt).inMilliseconds;
      if (ms > 0) durationMs = ms;
    }

    return ChatTurn(
      id: _turnId(user, index),
      user: user,
      work: List<ChatBlock>.unmodifiable(_work),
      finalText: _answer.trim(),
      notices: List<ChatBlock>.unmodifiable(_notices),
      running: running,
      actionCount: _countActions(_work),
      durationMs: durationMs,
      startedAt: startedAt,
      diffStats: _diffStats(_work),
      hasPendingApproval:
          pendingApprovalIds.isNotEmpty &&
          _work.any(
            (b) => b.kind == 'approval' && pendingApprovalIds.contains(b.id),
          ),
    );
  }

  static ChatBlock? _lastAssistant(List<ChatBlock> work) {
    for (var i = work.length - 1; i >= 0; i--) {
      final block = work[i];
      if (block.kind == 'assistant_markdown' && block.text.trim().isNotEmpty) {
        return block;
      }
    }
    return null;
  }

  DateTime? _firstWorkTime() {
    for (final block in _work) {
      final ts = _time(block.raw['created_at']);
      if (ts != null) return ts;
    }
    return null;
  }

  DateTime? _lastWorkTime() {
    for (var i = _work.length - 1; i >= 0; i--) {
      final ts = _time(_work[i].raw['created_at']);
      if (ts != null) return ts;
    }
    return null;
  }
}

class _FinalAnswerScanner {
  bool _inside = false;
  bool sawTag = false;

  _AnswerSplit feed(String text) {
    if (text.isEmpty) return const _AnswerSplit('', '');
    final work = StringBuffer();
    final answer = StringBuffer();
    var i = 0;

    while (i < text.length) {
      final tag = _inside ? chatFinalAnswerClose : chatFinalAnswerOpen;
      final at = text.indexOf(tag, i);
      if (at < 0) {
        var tail = text.substring(i);
        // Do not flash a half-received tag in the UI while it streams.
        final hold = _partialTagSuffix(tail, tag);
        if (hold > 0) tail = tail.substring(0, tail.length - hold);
        _append(tail, work, answer);
        break;
      }
      _append(text.substring(i, at), work, answer);
      sawTag = true;
      _inside = !_inside;
      i = at + tag.length;
    }
    return _AnswerSplit(work.toString(), answer.toString());
  }

  void _append(String chunk, StringBuffer work, StringBuffer answer) {
    if (chunk.isEmpty) return;
    if (_inside) {
      answer.write(chunk);
    } else {
      work.write(chunk);
    }
  }
}

class _AnswerSplit {
  const _AnswerSplit(this.work, this.answer);
  final String work;
  final String answer;
}

int _partialTagSuffix(String text, String tag) {
  final max = text.length < tag.length - 1 ? text.length : tag.length - 1;
  for (var n = max; n > 0; n--) {
    if (tag.startsWith(text.substring(text.length - n))) return n;
  }
  return 0;
}

String _turnId(ChatBlock? user, int index) {
  if (user == null) return 'turn-$index';
  final ts = user.raw['created_at']?.toString() ?? '';
  return 'u:$ts:${user.text.hashCode}';
}

int _countActions(List<ChatBlock> work) {
  var count = 0;
  final toolIds = <String>{};
  for (final block in work) {
    switch (block.kind) {
      case 'tool_call':
      case 'tool_call_delta':
        final id = block.id;
        if (id.isEmpty || toolIds.add(id)) count++;
      case 'thinking':
      case 'approval':
        count++;
      case 'subagent':
        if (block.raw['status'] != 'completed' ||
            block.raw['result_summary'] != null) {
          count++;
        }
    }
  }
  return count;
}

({int added, int removed})? _diffStats(List<ChatBlock> work) {
  var added = 0;
  var removed = 0;
  var found = false;
  for (final block in work) {
    if (block.kind != 'tool_result') continue;
    final stats = parseDiffStatsCached(block.raw['output']);
    if (stats == null) continue;
    found = true;
    added += stats.added;
    removed += stats.removed;
  }
  return found ? (added: added, removed: removed) : null;
}

DateTime? _time(Object? raw) {
  if (raw is DateTime) return raw;
  if (raw is String && raw.isNotEmpty) return DateTime.tryParse(raw);
  return null;
}
