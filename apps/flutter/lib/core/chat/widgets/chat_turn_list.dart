import 'package:flutter/material.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/chat/models/chat_block.dart';
import 'package:prodavan/core/chat/models/chat_turn.dart';
import 'package:prodavan/core/chat/widgets/blocks/chat_block_renderer.dart';
import 'package:prodavan/core/chat/widgets/blocks/chat_blocks.dart';
import 'package:prodavan/core/chat/widgets/chat_nav_rail.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// The chat transcript, one ListView item per turn.
///
/// A turn is a single immutable snapshot: user prompt, one collapsed work
/// summary, one final answer. There are no per-block entries, no
/// `findChildIndexCallback` and no grouping heuristics — ordering and collapse
/// state survive reloads, polls and streaming by construction.
class ChatTurnList extends StatefulWidget {
  const ChatTurnList({
    super.key,
    required this.turns,
    this.projectId,
    this.sessionId,
    this.api,
    this.onResolveApproval,
    this.hasMoreHistory = false,
    this.loadingHistory = false,
    this.onLoadOlder,
    this.turnStreaming = false,
    this.showWorkingIndicator = false,
    this.showReconnectIndicator = false,
    this.reconnectAttempt,
    this.reconnectMaxAttempts,
    this.reconnectNextModel,
    this.mcpAliases = const {},
    this.costResolver,
  });

  final List<ChatTurn> turns;
  final String? projectId;
  final String? sessionId;
  final ProdavanApi? api;
  final void Function(String approvalId, String decision)? onResolveApproval;
  final bool hasMoreHistory;
  final bool loadingHistory;
  final VoidCallback? onLoadOlder;
  final bool turnStreaming;
  final bool showWorkingIndicator;
  final bool showReconnectIndicator;
  final int? reconnectAttempt;
  final int? reconnectMaxAttempts;
  final String? reconnectNextModel;
  final Map<String, String> mcpAliases;
  final double? Function(String? model, int? inputTokens, int? outputTokens)?
  costResolver;

  @override
  State<ChatTurnList> createState() => _ChatTurnListState();
}

class _ChatTurnListState extends State<ChatTurnList> {
  final _scroll = ScrollController();
  final Map<String, GlobalKey> _turnKeys = {};
  final Set<String> _expanded = {};
  final Set<String> _collapsed = {};

  bool _pinnedToBottom = true;
  bool _didInitialBottom = false;
  bool _navRevealed = false;

  List<ChatTurn> get _visual => widget.turns.reversed.toList(growable: false);

  @override
  void initState() {
    super.initState();
    _scroll.addListener(_onScroll);
  }

  @override
  void dispose() {
    _scroll
      ..removeListener(_onScroll)
      ..dispose();
    super.dispose();
  }

  @override
  void didUpdateWidget(covariant ChatTurnList oldWidget) {
    super.didUpdateWidget(oldWidget);
    final oldLast = oldWidget.turns.isEmpty ? null : oldWidget.turns.last;
    final newLast = widget.turns.isEmpty ? null : widget.turns.last;
    // A genuinely new user prompt (not an older page prepend, not a poll
    // replacing the open turn) always brings the user back to the bottom.
    if (newLast != null && newLast.user != null && newLast.id != oldLast?.id) {
      _pinnedToBottom = true;
      _ensureBottom();
    }
  }

  void _onScroll() {
    if (!_scroll.hasClients) return;
    final pinned = _scroll.position.pixels <= 48;
    if (pinned != _pinnedToBottom) {
      _pinnedToBottom = pinned;
    }
  }

  void _ensureBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || !_scroll.hasClients || !_pinnedToBottom) return;
      if (_scroll.position.pixels > 0) _scroll.jumpTo(0);
    });
  }

  GlobalKey _keyFor(String id) =>
      _turnKeys.putIfAbsent(id, () => GlobalKey(debugLabel: 'turn-$id'));

  void _jumpTo(int visualIndex) {
    final visual = _visual;
    if (visual.isEmpty || visualIndex < 0 || visualIndex >= visual.length) {
      return;
    }
    if (_scroll.hasClients) {
      final max = _scroll.position.maxScrollExtent;
      final n = visual.length;
      final target = n <= 1 ? 0.0 : (visualIndex / (n - 1)) * max;
      _scroll.jumpTo(target.clamp(0.0, max));
    }
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final ctx = _keyFor(visual[visualIndex].id).currentContext;
      if (ctx != null && ctx.mounted) {
        Scrollable.ensureVisible(ctx, alignment: 0.08);
      }
    });
  }

  void _toggle(String id, bool expanded) {
    setState(() {
      if (expanded) {
        _expanded.add(id);
        _collapsed.remove(id);
      } else {
        _expanded.remove(id);
        _collapsed.add(id);
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final visual = _visual;
    // Keep per-turn keys/expansion for the live turns only.
    final liveIds = visual.map((t) => t.id).toSet();
    _turnKeys.removeWhere((id, _) => !liveIds.contains(id));
    _expanded.removeWhere((id) => !liveIds.contains(id));
    _collapsed.removeWhere((id) => !liveIds.contains(id));

    if (!_didInitialBottom && visual.isNotEmpty) {
      _didInitialBottom = true;
      _pinnedToBottom = true;
      _ensureBottom();
    }

    final anyRunning = visual.any((t) => t.running);
    final showIndicator =
        widget.showReconnectIndicator ||
        (widget.showWorkingIndicator && !anyRunning);
    final extra = showIndicator ? 1 : 0;
    final loadMore = widget.hasMoreHistory ? 1 : 0;

    final prompts = <ChatNavPrompt>[
      for (var i = 0; i < visual.length; i++)
        if (visual[i].user != null && visual[i].user!.text.trim().isNotEmpty)
          ChatNavPrompt(visualIndex: i, text: visual[i].user!.text),
    ];

    return MouseRegion(
      onEnter: (_) => setState(() => _navRevealed = true),
      onExit: (_) => setState(() => _navRevealed = false),
      child: Stack(
        children: [
          NotificationListener<ScrollNotification>(
            onNotification: (n) {
              if (n is ScrollUpdateNotification ||
                  n is UserScrollNotification) {
                _pinnedToBottom = n.metrics.pixels <= 48;
              }
              return false;
            },
            child: ListView.builder(
              controller: _scroll,
              reverse: true,
              padding: EdgeInsets.fromLTRB(
                AppSpacing.md,
                AppSpacing.md,
                AppSpacing.md + 18,
                AppSpacing.md,
              ),
              itemCount: visual.length + extra + loadMore,
              itemBuilder: (context, index) {
                if (showIndicator && index == 0) {
                  final reconnect = widget.showReconnectIndicator;
                  return SizedBox(
                    key: ValueKey(
                      reconnect
                          ? 'agent-reconnect-indicator'
                          : 'agent-working-indicator',
                    ),
                    child: AgentWorkingIndicator(
                      reconnect: reconnect
                          ? ReconnectIndicatorData(
                              attempt: widget.reconnectAttempt,
                              maxAttempts: widget.reconnectMaxAttempts,
                              nextModel: widget.reconnectNextModel,
                            )
                          : null,
                    ),
                  );
                }
                final vi = index - extra;
                if (vi >= visual.length) {
                  return _LoadOlderTile(
                    loading: widget.loadingHistory,
                    onLoadOlder: widget.onLoadOlder,
                  );
                }
                final turn = visual[vi];
                final autoExpanded =
                    turn.hasPendingApproval && !_collapsed.contains(turn.id);
                final expanded = _expanded.contains(turn.id) || autoExpanded;
                return SelectionArea(
                  key: ValueKey('turn-${turn.id}'),
                  child: _TurnView(
                    key: _keyFor(turn.id),
                    turn: turn,
                    expanded: expanded,
                    onToggle: () => _toggle(turn.id, !expanded),
                    projectId: widget.projectId,
                    sessionId: widget.sessionId,
                    api: widget.api,
                    onResolveApproval: widget.onResolveApproval,
                    mcpAliases: widget.mcpAliases,
                    costResolver: widget.costResolver,
                    turnStreaming: widget.turnStreaming || turn.running,
                  ),
                );
              },
            ),
          ),
          if (prompts.isNotEmpty)
            Positioned(
              top: 0,
              bottom: 0,
              right: 0,
              width: 40,
              child: ChatNavRail(
                prompts: prompts,
                entryCount: visual.length,
                revealed: _navRevealed,
                onJump: _jumpTo,
              ),
            ),
          if (widget.loadingHistory)
            const Positioned(
              top: 8,
              left: 0,
              right: 0,
              child: Center(
                child: SizedBox(
                  width: 20,
                  height: 20,
                  child: CircularProgressIndicator(strokeWidth: 2),
                ),
              ),
            ),
        ],
      ),
    );
  }
}

class _TurnView extends StatelessWidget {
  const _TurnView({
    super.key,
    required this.turn,
    required this.expanded,
    required this.onToggle,
    required this.turnStreaming,
    this.projectId,
    this.sessionId,
    this.api,
    this.onResolveApproval,
    this.mcpAliases = const {},
    this.costResolver,
  });

  final ChatTurn turn;
  final bool expanded;
  final VoidCallback onToggle;
  final bool turnStreaming;
  final String? projectId;
  final String? sessionId;
  final ProdavanApi? api;
  final void Function(String approvalId, String decision)? onResolveApproval;
  final Map<String, String> mcpAliases;
  final double? Function(String? model, int? inputTokens, int? outputTokens)?
  costResolver;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final scheme = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.only(bottom: AppSpacing.md),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (turn.user != null)
            ChatBlockRenderer(
              block: turn.user!,
              projectId: projectId,
              sessionId: sessionId,
              api: api,
              costResolver: costResolver,
              mcpAliases: mcpAliases,
            ),
          if (turn.work.isNotEmpty) ...[
            const SizedBox(height: 4),
            _WorkHeader(
              turn: turn,
              expanded: expanded,
              onToggle: onToggle,
              label: _workLabel(l10n),
              scheme: scheme,
            ),
            if (expanded)
              Padding(
                padding: const EdgeInsets.only(left: 24, top: 4),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: _workWidgets(context),
                ),
              ),
          ],
          if (turn.hasFinal)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: turn.running
                  ? ChatStreamingMarkdown(text: turn.finalText)
                  : ChatMarkdownBody(text: turn.finalText),
            ),
          for (final notice in turn.notices)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: ChatBlockRenderer(
                block: notice,
                projectId: projectId,
                sessionId: sessionId,
                api: api,
                onResolveApproval: onResolveApproval,
                costResolver: costResolver,
                mcpAliases: mcpAliases,
                turnStreaming: turnStreaming,
              ),
            ),
        ],
      ),
    );
  }

  String _workLabel(AppLocalizations l10n) {
    if (turn.running) return l10n.projectChatWorking;
    final count = turn.actionCount > 0 ? turn.actionCount : turn.work.length;
    final duration = _formatDuration(turn.durationMs);
    return duration == null
        ? l10n.projectChatWorked(count)
        : l10n.projectChatWorkedFor(duration, count);
  }

  /// Pair tool calls with their results so an expanded turn shows one panel
  /// per tool instead of two raw rows.
  List<Widget> _workWidgets(BuildContext context) {
    final results = <String, ChatBlock>{};
    for (final block in turn.work) {
      if (block.kind == 'tool_result' && block.id.isNotEmpty) {
        results.putIfAbsent(block.id, () => block);
      }
    }
    final callIds = {
      for (final block in turn.work)
        if (block.kind == 'tool_call') block.id,
    };
    final usedResults = <String>{};
    final out = <Widget>[];

    for (final block in turn.work) {
      if (block.kind == 'tool_call_delta' && callIds.contains(block.id)) {
        continue;
      }
      ChatBlock? paired;
      if (block.kind == 'tool_call') {
        paired = results[block.id];
        if (paired != null) usedResults.add(block.id);
      } else if (block.kind == 'tool_result') {
        if (block.id.isNotEmpty && results[block.id] == block) {
          // Rendered together with its call.
          continue;
        }
        if (usedResults.contains(block.id)) continue;
      }
      out.add(
        Padding(
          padding: const EdgeInsets.only(bottom: 6),
          child: ChatBlockRenderer(
            block: block,
            pairedToolResult: paired,
            projectId: projectId,
            sessionId: sessionId,
            api: api,
            onResolveApproval: onResolveApproval,
            costResolver: costResolver,
            mcpAliases: mcpAliases,
            turnStreaming: turnStreaming,
          ),
        ),
      );
    }
    return out;
  }
}

class _WorkHeader extends StatelessWidget {
  const _WorkHeader({
    required this.turn,
    required this.expanded,
    required this.onToggle,
    required this.label,
    required this.scheme,
  });

  final ChatTurn turn;
  final bool expanded;
  final VoidCallback onToggle;
  final String label;
  final ColorScheme scheme;

  @override
  Widget build(BuildContext context) {
    final stats = turn.diffStats;
    return Semantics(
      button: true,
      expanded: expanded,
      label: label,
      child: InkWell(
        onTap: onToggle,
        borderRadius: BorderRadius.circular(8),
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 6, horizontal: 2),
          child: Row(
            children: [
              SizedBox(
                width: 16,
                height: 16,
                child: turn.running
                    ? const CircularProgressIndicator(strokeWidth: 1.6)
                    : Icon(
                        Icons.task_alt,
                        size: 16,
                        color: scheme.onSurfaceVariant,
                      ),
              ),
              const SizedBox(width: 8),
              Flexible(
                child: Text(
                  label,
                  style: Theme.of(context).textTheme.labelLarge
                      ?.copyWith(color: scheme.onSurfaceVariant),
                ),
              ),
              if (stats != null) ...[
                const SizedBox(width: 8),
                Text(
                  '+${stats.added}',
                  style: TextStyle(
                    fontSize: 12,
                    color: scheme.primary,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                const SizedBox(width: 2),
                Text(
                  '−${stats.removed}',
                  style: TextStyle(
                    fontSize: 12,
                    color: scheme.error,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ],
              const SizedBox(width: 4),
              AnimatedRotation(
                turns: expanded ? 0.5 : 0,
                duration: const Duration(milliseconds: 140),
                child: Icon(
                  Icons.expand_more,
                  size: 18,
                  color: scheme.onSurfaceVariant,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _LoadOlderTile extends StatelessWidget {
  const _LoadOlderTile({required this.loading, required this.onLoadOlder});

  final bool loading;
  final VoidCallback? onLoadOlder;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: TextButton.icon(
        onPressed: loading ? null : onLoadOlder,
        icon: loading
            ? const SizedBox(
                width: 14,
                height: 14,
                child: CircularProgressIndicator(strokeWidth: 2),
              )
            : const Icon(Icons.history, size: 16),
        label: const Text('Загрузить ещё'),
      ),
    );
  }
}

String? _formatDuration(int? ms) {
  if (ms == null || ms < 1000) return null;
  final duration = Duration(milliseconds: ms);
  if (duration.inHours > 0) {
    return '${duration.inHours} ч ${duration.inMinutes % 60} мин';
  }
  if (duration.inMinutes > 0) {
    return '${duration.inMinutes} мин ${duration.inSeconds % 60} с';
  }
  return '${duration.inSeconds} с';
}
