import 'package:desktop_drop/desktop_drop.dart';
import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/chat/controller/chat_session_controller.dart';
import 'package:prodavan/core/chat/models/chat_checklist.dart';
import 'package:prodavan/core/chat/models/chat_block.dart';
import 'package:prodavan/core/chat/widgets/blocks/chat_block_renderer.dart';
import 'package:prodavan/core/chat/widgets/blocks/chat_blocks.dart';
import 'package:prodavan/core/chat/widgets/chat_checklist_panel.dart';
import 'package:prodavan/core/chat/widgets/chat_display_grouping.dart';
import 'package:prodavan/core/chat/widgets/chat_empty_placeholder.dart';
import 'package:prodavan/core/chat/widgets/chat_nav_rail.dart';
import 'package:prodavan/core/chat/widgets/chat_transcript_skeleton.dart';
import 'package:prodavan/core/chat/widgets/chat_turn_list.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/features/employee/widgets/chat_composer.dart';
import 'package:prodavan/l10n/app_localizations.dart';

const _kStickThreshold = 48.0;

int _blocksScrollFingerprint(List<ChatBlock> blocks) {
  if (blocks.isEmpty) return 0;
  final last = blocks.last;
  return Object.hash(
    blocks.length,
    last.key,
    last.text.length,
    last.isStreaming,
  );
}

/// Stable identity of a visual entry. The key must sit on the direct ListView
/// child: a reverse list prepends blocks, and index-matched children would
/// destroy the element subtree (with every collapsible state) on each prepend.
String _displayEntryKey(ChatDisplayEntry entry) {
  return switch (entry) {
    ChatDisplaySingle(:final item) => 'single:${item.block.key}',
    ChatDisplayGroup(:final kind, :final items) =>
      'group:${kind.name}:${items.first.block.key}',
    ChatDisplayActivity(:final items) => 'activity:${items.first.block.key}',
  };
}

/// Signature of a rendered entry for the widget cache.
///
/// Identity hash of the block's raw map is O(1) and changes exactly when the
/// block is replaced (streaming text growth, usage attach, finalize) — no giant
/// key strings built per frame, and no stale content.
String _entrySignature(ChatDisplayEntry entry) {
  switch (entry) {
    case ChatDisplaySingle(:final item):
      return 's:${item.block.key}:${identityHashCode(item.block.raw)}:'
          '${item.paired?.key ?? ''}:'
          '${item.paired == null ? 0 : identityHashCode(item.paired!.raw)}';
    case ChatDisplayGroup(:final kind, :final items):
      return 'g:${kind.name}:${items.length}:${items.first.block.key}:'
          '${items.last.block.key}:${identityHashCode(items.last.block.raw)}';
    case ChatDisplayActivity(:final items, :final streaming, :final contentHash):
      return 'a:${items.first.block.key}:${items.length}:${streaming ? 1 : 0}:'
          '$contentHash';
  }
}

/// Unique display keys for the whole timeline.
///
/// Block keys are unique for transcript/live blocks, but a caller can build
/// blocks ad-hoc (tests, future projections). Two children with the same key
/// corrupt a virtualized list's element reuse — the exact failure mode that
/// made the chat "go insane" — so collisions get an occurrence suffix.
List<String> _uniqueEntryKeys(List<ChatDisplayEntry> entries) {
  final used = <String, int>{};
  final out = <String>[];
  for (final entry in entries) {
    final base = _displayEntryKey(entry);
    final n = used[base] ?? 0;
    used[base] = n + 1;
    out.add(n == 0 ? base : '$base#$n');
  }
  return out;
}

String _groupLabel(AppLocalizations l10n, ActivityGroupKind kind, int count) {
  return switch (kind) {
    ActivityGroupKind.thinking => l10n.projectChatGroupThinking(count),
    ActivityGroupKind.fileEdit => l10n.projectChatGroupFilesEdited(count),
    ActivityGroupKind.fileRead => l10n.projectChatGroupFilesRead(count),
    ActivityGroupKind.fileDelete => l10n.projectChatGroupDeleted(count),
    ActivityGroupKind.searchGlob => l10n.projectChatGroupGlob(count),
    ActivityGroupKind.searchGrep => l10n.projectChatGroupGrep(count),
    ActivityGroupKind.listDir => l10n.projectChatGroupListDir(count),
    ActivityGroupKind.command => l10n.projectChatGroupCommands(count),
    ActivityGroupKind.mcp => l10n.projectChatGroupMcp(count),
    ActivityGroupKind.generic => l10n.projectChatGroupGeneric(count),
  };
}

/// Collapsed aggregation of everything the agent did on the way to its answer.
///
/// One entry per contiguous run; the final answer always stays open. The State
/// is preserved across rebuilds (stable entry key), so an expanded activity does
/// not snap shut when a new tool call arrives.
class _ActivityBlock extends StatefulWidget {
  const _ActivityBlock({
    required this.label,
    required this.items,
    required this.childBuilder,
    this.streaming = false,
    this.diffStats,
  });

  final String label;
  final List<ChatDisplayPair> items;
  final Widget Function(ChatDisplayPair item) childBuilder;
  final bool streaming;
  final ({int added, int removed})? diffStats;

  @override
  State<_ActivityBlock> createState() => _ActivityBlockState();
}

class _ActivityBlockState extends State<_ActivityBlock> {
  bool _open = false;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final scheme = Theme.of(context).colorScheme;
    final stats = widget.diffStats;
    Widget? badge;
    if (stats != null && (stats.added > 0 || stats.removed > 0)) {
      badge = Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (stats.added > 0)
            Text(
              '+${stats.added}',
              style: TextStyle(color: scheme.primary, fontSize: 12),
            ),
          if (stats.added > 0 && stats.removed > 0) const SizedBox(width: 4),
          if (stats.removed > 0)
            Text(
              '-${stats.removed}',
              style: TextStyle(color: scheme.error, fontSize: 12),
            ),
        ],
      );
    }

    // Inner grouping is only needed while expanded. A collapsed activity can
    // hold hundreds of tool rows; computing their sub-groups on every frame
    // made a long transcript needlessly expensive.
    final innerEntries = _open
        ? groupInnerWorkItems(widget.items)
        : const <ChatDisplayEntry>[];

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        ChatMutedLine(
          label: widget.label,
          trailing: widget.streaming
              ? const SizedBox(
                  width: 12,
                  height: 12,
                  child: CircularProgressIndicator(strokeWidth: 1.6),
                )
              : badge,
          expanded: _open,
          onTap: () => setState(() => _open = !_open),
        ),
        if (_open)
          ChatInsetPanel(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                for (final inner in innerEntries)
                  switch (inner) {
                    ChatDisplaySingle(:final item) => widget.childBuilder(item),
                    ChatDisplayGroup(:final kind, :final items) =>
                      GroupedActivityBlock(
                        kind: kind,
                        items: items,
                        label: _groupLabel(l10n, kind, items.length),
                        diffStats: aggregateDiffStats(items),
                        childBuilder: widget.childBuilder,
                      ),
                    ChatDisplayActivity() => const SizedBox.shrink(),
                  },
              ],
            ),
          ),
      ],
    );
  }
}

class ChatMessageList extends StatefulWidget {
  const ChatMessageList({
    super.key,
    required this.blocks,
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
    this.structureRevision,
  });

  final List<ChatBlock> blocks;
  final String? projectId;
  final String? sessionId;
  final ProdavanApi? api;
  final void Function(String approvalId, String decision)? onResolveApproval;
  final bool hasMoreHistory;
  final bool loadingHistory;
  final VoidCallback? onLoadOlder;
  final bool turnStreaming;

  /// "agentscale работает…" while the turn streams but the agent is silent.
  final bool showWorkingIndicator;

  /// «Попытка реконнекта…» while the runtime waits out a provider error
  /// (chat error policy) before retrying the model call.
  final bool showReconnectIndicator;
  final int? reconnectAttempt;
  final int? reconnectMaxAttempts;
  final String? reconnectNextModel;

  /// Cost estimate for assistant usage metadata (runtime cost wins).
  final double? Function(String? model, int? inputTokens, int? outputTokens)?
  costResolver;

  /// MCP tool display aliases (mcp_aliases module meta) — friendly tool
  /// labels in tool activity/approval blocks.
  final Map<String, String> mcpAliases;

  /// Controller-owned structural revision: the timeline never changes while it
  /// stays the same (text growth is handled per-entry), so grouping is not
  /// recomputed on hover/scroll.
  final int? structureRevision;

  @override
  State<ChatMessageList> createState() => ChatMessageListState();
}

class ChatMessageListState extends State<ChatMessageList> {
  final _scroll = ScrollController();
  bool _pinnedToBottom = true;

  // Кэш отрисованных записей. Финализированные (не стримящие) блоки
  // иммутабельны: переиспользуем их виджет, и Flutter пропускает поддерево
  // (identical widget).
  final Map<String, Widget> _entryCache = {};
  Object? _cacheApi;
  Object? _cacheCostResolver;
  Object? _cacheAliases;
  Object? _cacheResolveApproval;
  Locale? _cacheLocale;
  bool _didInitialBottom = false;
  int _lastFingerprint = 0;
  int _lastBlockCount = 0;

  // ---- защита автоподгрузки от бесконечного цикла ----
  static const _kMaxAutoLoadRounds = 3;
  int _autoLoadBlocks = 0;
  int _autoLoadRounds = 0;
  bool _autoLoadInFlight = false;
  bool _autoLoadPaused = false;

  // Кэш группировки: см. [_entriesFor].
  List<ChatDisplayEntry>? _entriesCache;
  int? _entriesCacheKey;

  /// Индекс верхнеуровневого ребёнка по его ключу — нужен
  /// `findChildIndexCallback`, чтобы при подгрузке истории сверху
  /// существующие элементы не пересоздавались.
  final Map<Key, int> _childIndexByKey = {};

  /// Anchor keys for jump-to-prompt navigation (visual index → context).
  final Map<String, GlobalKey> _jumpKeys = {};

  /// True while the pointer is in the transcript — reveals the nav rail.
  bool _navRevealed = false;

  @override
  void initState() {
    super.initState();
    _scroll.addListener(_onScroll);
    _lastFingerprint = _blocksScrollFingerprint(widget.blocks);
    _lastBlockCount = widget.blocks.length;
  }

  /// Chat pattern (Telegram / Ollama / Discord): reverse list — visual bottom
  /// (newest) sits at pixels ≈ 0. Pin is natural; no jumpTo(max) crutches.
  bool _isPinnedToBottom([ScrollMetrics? metrics]) {
    final m = metrics ?? (_scroll.hasClients ? _scroll.position : null);
    if (m == null) return true;
    return m.pixels <= _kStickThreshold;
  }

  void _ensureBottom({bool animate = false}) {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || !_scroll.hasClients || !_pinnedToBottom) return;
      if (_scroll.position.pixels <= _kStickThreshold) return;
      if (animate) {
        _scroll.animateTo(
          0,
          duration: const Duration(milliseconds: 120),
          curve: Curves.easeOut,
        );
      } else {
        _scroll.jumpTo(0);
      }
    });
  }

  void _maybeAutoloadOlder() {
    if (!_scroll.hasClients) return;
    if (_autoLoadPaused || _autoLoadInFlight) return;
    if (!widget.hasMoreHistory ||
        widget.loadingHistory ||
        widget.onLoadOlder == null) {
      return;
    }
    final pos = _scroll.position;
    final contentFillsViewport = pos.maxScrollExtent > pos.viewportDimension;
    if (contentFillsViewport) return;

    final loaded = widget.blocks.length;
    if (loaded == _autoLoadBlocks) {
      _autoLoadRounds++;
      if (_autoLoadRounds >= _kMaxAutoLoadRounds) {
        _autoLoadPaused = true;
        return;
      }
    } else {
      _autoLoadRounds = 0;
      _autoLoadBlocks = loaded;
    }
    _autoLoadInFlight = true;
    widget.onLoadOlder!();
  }

  void _onScroll() {
    if (!_scroll.hasClients) return;
    final pos = _scroll.position;
    _pinnedToBottom = _isPinnedToBottom(pos);
    if (pos.pixels >= pos.maxScrollExtent - _kStickThreshold &&
        widget.hasMoreHistory &&
        !widget.loadingHistory &&
        widget.onLoadOlder != null) {
      _autoLoadPaused = false;
      _autoLoadRounds = 0;
      widget.onLoadOlder!();
    }
  }

  int _groupingKey(int fingerprint) => Object.hash(
    widget.structureRevision ?? fingerprint,
    widget.turnStreaming,
  );

  /// Grouping is O(n) over the whole history; cache it by structural revision
  /// (or a cheap fingerprint when the widget is used standalone).
  List<ChatDisplayEntry> _entriesFor(
    List<ChatBlock> blocks, {
    required bool turnStreaming,
    required int fingerprint,
  }) {
    final key = _groupingKey(fingerprint);
    if (_entriesCacheKey == key && _entriesCache != null) {
      return _entriesCache!;
    }
    final built = groupDisplayEntries(blocks, turnStreaming: turnStreaming);
    _entriesCache = built;
    _entriesCacheKey = key;
    return built;
  }

  Widget _renderPair(ChatDisplayPair item) {
    final block = item.block;
    return ChatBlockRenderer(
      key: ValueKey(block.key),
      block: block,
      pairedToolResult: item.paired,
      projectId: widget.projectId,
      sessionId: widget.sessionId,
      api: widget.api,
      onResolveApproval: widget.onResolveApproval,
      costResolver: widget.costResolver,
      mcpAliases: widget.mcpAliases,
      turnStreaming: widget.turnStreaming,
    );
  }

  Widget _cachedEntry(
    AppLocalizations l10n,
    ChatDisplayEntry entry,
    String entryKey,
    Set<String> liveKeys,
  ) {
    final sig = '${_entrySignature(entry)}|$entryKey';
    liveKeys.add(sig);
    final streaming = switch (entry) {
      ChatDisplaySingle(:final item) => item.block.isStreaming,
      ChatDisplayGroup(:final items) => items.any((i) => i.block.isStreaming),
      ChatDisplayActivity(:final streaming) => streaming,
    };
    if (streaming) return _renderEntry(l10n, entry, entryKey);
    final cached = _entryCache[sig];
    if (cached != null) return cached;
    final built = _renderEntry(l10n, entry, entryKey);
    _entryCache[sig] = built;
    return built;
  }

  Widget _renderEntry(
    AppLocalizations l10n,
    ChatDisplayEntry entry,
    String entryKey,
  ) {
    // Anchor only user prompts: they are the navigation targets. Anchor keys
    // on every row would re-parent thousands of GlobalKeys while the
    // virtualized list jumps, which is both wasteful and fragile.
    final isUserPrompt =
        entry is ChatDisplaySingle && entry.item.block.kind == 'user';
    final jumpKey = isUserPrompt
        ? _jumpKeys.putIfAbsent(entryKey, GlobalKey.new)
        : null;
    final body = SelectionArea(
      child: switch (entry) {
        ChatDisplaySingle(:final item) => _renderPair(item),
        ChatDisplayGroup(:final kind, :final items) => GroupedActivityBlock(
          kind: kind,
          items: items,
          label: _groupLabel(l10n, kind, items.length),
          diffStats: aggregateDiffStats(items),
          childBuilder: _renderPair,
        ),
        ChatDisplayActivity(:final items, :final streaming, :final diffStats) =>
          _ActivityBlock(
            label: streaming
                ? l10n.projectChatWorking
                : l10n.projectChatWorked(
                    workActionCount(items).clamp(1, 1 << 30),
                  ),
            items: items,
            streaming: streaming,
            diffStats: diffStats,
            childBuilder: _renderPair,
          ),
      },
    );
    return KeyedSubtree(
      key: ValueKey(entryKey),
      child: jumpKey == null ? body : Container(key: jumpKey, child: body),
    );
  }

  @override
  void didUpdateWidget(covariant ChatMessageList oldWidget) {
    super.didUpdateWidget(oldWidget);
    final fp = _blocksScrollFingerprint(widget.blocks);
    final countGrew = widget.blocks.length > _lastBlockCount;
    final prepended =
        countGrew &&
        (widget.loadingHistory ||
            oldWidget.loadingHistory ||
            (widget.blocks.isNotEmpty &&
                oldWidget.blocks.isNotEmpty &&
                widget.blocks.first.id != oldWidget.blocks.first.id &&
                widget.blocks.last.id == oldWidget.blocks.last.id));

    if (prepended || widget.loadingHistory || oldWidget.loadingHistory) {
      _lastFingerprint = fp;
      _lastBlockCount = widget.blocks.length;
      if (oldWidget.loadingHistory && !widget.loadingHistory) {
        _autoLoadInFlight = false;
        WidgetsBinding.instance.addPostFrameCallback((_) {
          if (mounted) _maybeAutoloadOlder();
        });
      }
      return;
    }

    if (fp != _lastFingerprint) {
      final wasPinned = _scroll.hasClients
          ? _isPinnedToBottom()
          : _pinnedToBottom;
      _lastFingerprint = fp;
      _lastBlockCount = widget.blocks.length;
      _pinnedToBottom = wasPinned;
      if (wasPinned) {
        _ensureBottom(animate: _didInitialBottom);
      }
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) _maybeAutoloadOlder();
      });
      return;
    }

    _lastBlockCount = widget.blocks.length;
  }

  @override
  void dispose() {
    _scroll.removeListener(_onScroll);
    _scroll.dispose();
    super.dispose();
  }

  /// Build the prompt markers for the hover navigation rail.
  List<ChatNavPrompt> _navPrompts(List<ChatDisplayEntry> visual) {
    final out = <ChatNavPrompt>[];
    for (var i = 0; i < visual.length; i++) {
      final entry = visual[i];
      if (entry is ChatDisplaySingle && entry.item.block.kind == 'user') {
        final text = entry.item.block.text.trim();
        if (text.isEmpty) continue;
        out.add(ChatNavPrompt(visualIndex: i, text: text));
      }
    }
    return out;
  }

  List<ChatDisplayEntry> _currentVisualEntries() => _entriesFor(
    widget.blocks,
    turnStreaming: widget.turnStreaming,
    fingerprint: _blocksScrollFingerprint(widget.blocks),
  ).reversed.toList(growable: false);

  /// Jump to a visual entry (0 = newest, bottom of the reverse list).
  ///
  /// The list is virtualized, so a far target is not built and
  /// `ensureVisible` cannot find it. We jump to the proportional offset, then
  /// correct using the window of entries that actually built, until the target
  /// is on screen (bounded — the user never waits on a long loop).
  ///
  /// Target identity is the entry key, not the index: the agent may append
  /// rows between frames, which shifts indices while the jump is in flight.
  Future<void> jumpToVisualIndex(int index) async {
    if (!_scroll.hasClients) return;
    var visual = _currentVisualEntries();
    if (visual.isEmpty) return;
    var visualKeys = _uniqueEntryKeys(visual);
    final targetKey = visualKeys[index.clamp(0, visual.length - 1)];
    var target = visualKeys.indexOf(targetKey);
    if (target < 0) return;

    bool refreshTarget() {
      final next = _currentVisualEntries();
      final nextKeys = _uniqueEntryKeys(next);
      final nextTarget = nextKeys.indexOf(targetKey);
      if (nextTarget < 0) return false;
      visual = next;
      visualKeys = nextKeys;
      target = nextTarget;
      return true;
    }

    if (_tryEnsureVisible(target, visualKeys)) return;

    final max = _scroll.position.maxScrollExtent;
    if (max <= 0) return;
    final denom = visual.length > 1 ? visual.length - 1 : 1;
    var offset = (target / denom) * max;

    for (var attempt = 0; attempt < 7; attempt++) {
      _scroll.jumpTo(offset.clamp(0.0, max).toDouble());
      await SchedulerBinding.instance.endOfFrame;
      if (!mounted || !_scroll.hasClients) return;
      if (!refreshTarget()) return;
      final currentMax = _scroll.position.maxScrollExtent;
      if (currentMax > 0) {
        offset = offset.clamp(0.0, currentMax).toDouble();
      }
      if (_tryEnsureVisible(target, visualKeys)) return;

      var minBuilt = visual.length;
      var maxBuilt = -1;
      for (var i = 0; i < visualKeys.length; i++) {
        if (_anchorContext(visualKeys[i]) != null) {
          if (i < minBuilt) minBuilt = i;
          if (i > maxBuilt) maxBuilt = i;
        }
      }
      if (maxBuilt < 0) return;
      // Anchor keys exist only on user prompts, so minBuilt/maxBuilt are rows
      // of visible prompts. The visible window spans that row distance, which
      // gives a good per-row height estimate even for variable message sizes.
      final span = maxBuilt - minBuilt;
      final avgHeight = span >= 2
          ? _scroll.position.viewportDimension / span
          : _scroll.position.viewportDimension / 8;
      if (target < minBuilt) {
        offset -= (minBuilt - target) * avgHeight;
      } else if (target > maxBuilt) {
        offset += (target - maxBuilt) * avgHeight;
      } else {
        return;
      }
      offset = offset.clamp(0.0, currentMax > 0 ? currentMax : max).toDouble();
    }
  }

  /// A GlobalKey can briefly outlive its element (list virtualization keeps the
  /// key around while the row is deactivated). Using such a context crashes in
  /// `findRenderObject`; only attached, mounted anchors count.
  BuildContext? _anchorContext(String entryKey) {
    final ctx = _jumpKeys[entryKey]?.currentContext;
    if (ctx == null || !ctx.mounted) return null;
    try {
      final ro = ctx.findRenderObject();
      if (ro == null || !ro.attached) return null;
    } catch (_) {
      return null;
    }
    return ctx;
  }

  bool _tryEnsureVisible(int index, List<String> visualKeys) {
    final ctx = _anchorContext(visualKeys[index]);
    if (ctx == null) return false;
    // Zero-duration jump: an animation started from an async jump loop races
    // layout/semantics and can assert in debug builds. The target row is
    // already built here, so a direct reposition is instant and safe.
    Scrollable.ensureVisible(ctx, alignment: 0.08);
    return true;
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final locale = Localizations.localeOf(context);
    final fp = _blocksScrollFingerprint(widget.blocks);
    final entries = _entriesFor(
      widget.blocks,
      turnStreaming: widget.turnStreaming,
      fingerprint: fp,
    );

    if (!_didInitialBottom && widget.blocks.isNotEmpty) {
      _didInitialBottom = true;
      _pinnedToBottom = true;
      _ensureBottom();
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) _maybeAutoloadOlder();
      });
    }

    if (!identical(_cacheApi, widget.api) ||
        !identical(_cacheCostResolver, widget.costResolver) ||
        !identical(_cacheAliases, widget.mcpAliases) ||
        !identical(_cacheResolveApproval, widget.onResolveApproval) ||
        _cacheLocale != locale) {
      // Пропсы, от которых зависит отрисовка блоков, сменились — кэш невалиден.
      _entryCache.clear();
      _cacheApi = widget.api;
      _cacheCostResolver = widget.costResolver;
      _cacheAliases = widget.mcpAliases;
      _cacheResolveApproval = widget.onResolveApproval;
      _cacheLocale = locale;
    }

    // The working indicator is the newest thing in the list while it shows.
    final visual = entries.reversed.toList(growable: false);
    final visualKeys = _uniqueEntryKeys(visual);
    final liveKeys = <String>{};
    final children = <Widget>[
      if (widget.showReconnectIndicator)
        SizedBox(
          key: const ValueKey('agent-reconnect-indicator'),
          child: AgentWorkingIndicator(
            reconnect: ReconnectIndicatorData(
              attempt: widget.reconnectAttempt,
              maxAttempts: widget.reconnectMaxAttempts,
              nextModel: widget.reconnectNextModel,
            ),
          ),
        )
      else if (widget.showWorkingIndicator)
        const SizedBox(
          key: ValueKey('agent-working-indicator'),
          child: AgentWorkingIndicator(),
        ),
      for (var i = 0; i < visual.length; i++)
        _cachedEntry(l10n, visual[i], visualKeys[i], liveKeys),
    ];
    if (_entryCache.isNotEmpty) {
      _entryCache.removeWhere((k, _) => !liveKeys.contains(k));
    }
    // Карта «ключ → индекс» для findChildIndexCallback: при подгрузке истории
    // индексы всех существующих записей сдвигаются, и без этой карты Flutter
    // пересоздал бы каждый элемент (теряя состояние схлопнутых блоков).
    _childIndexByKey
      ..clear()
      ..addEntries(
        children.indexed
            .where((e) => e.$2.key != null)
            .map((e) => MapEntry(e.$2.key!, e.$1)),
      );

    final prompts = _navPrompts(visual);
    // Prune anchor keys of entries that no longer exist.
    if (_jumpKeys.length > visualKeys.length * 2 + 16) {
      final live = visualKeys.toSet();
      _jumpKeys.removeWhere((k, _) => !live.contains(k));
    }

    return MouseRegion(
      onEnter: (_) => setState(() => _navRevealed = true),
      onExit: (_) => setState(() => _navRevealed = false),
      child: Stack(
        children: [
          NotificationListener<ScrollNotification>(
            onNotification: (n) {
              if (n is ScrollUpdateNotification ||
                  n is UserScrollNotification) {
                _pinnedToBottom = _isPinnedToBottom(n.metrics);
              }
              return false;
            },
            // Per-item SelectionArea keeps drag-to-select working inside the scrollable
            // (a single wrap around the ListView eats drag gestures). Entry keys are
            // globally unique (see _uniqueEntryKeys), so recycled rows never duplicate
            // keys — the failure that previously corrupted the element tree.
            child: ListView.builder(
              controller: _scroll,
              reverse: true,
              padding: EdgeInsets.fromLTRB(
                AppSpacing.md,
                AppSpacing.md,
                AppSpacing.md + 18,
                AppSpacing.md,
              ),
              itemCount: children.length,
              findChildIndexCallback: (Key key) {
                final idx = _childIndexByKey[key];
                return idx;
              },
              itemBuilder: (context, index) => children[index],
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
                onJump: jumpToVisualIndex,
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

class ChatScaffold extends StatefulWidget {
  const ChatScaffold({
    super.key,
    required this.controller,
    required this.api,
    required this.chatSendable,
    required this.loading,
    required this.title,
    this.onOpenChatSettings,
    this.onSessionMaterialized,
    this.onDraftPresenceChanged,
    this.modelLabel,
    this.onPickModel,
    this.disabledHint,
    this.wakeMode = false,
    this.waking = false,
    this.onWake,
    this.updateMode = false,
    this.updating = false,
    this.onUpdate,
    this.onDismissUpdate,
  });

  final ChatSessionController controller;
  final ProdavanApi api;
  final bool chatSendable;
  final bool loading;
  final VoidCallback? onOpenChatSettings;
  final void Function(String sessionId)? onSessionMaterialized;
  final VoidCallback? onDraftPresenceChanged;

  /// Label for the composer's quick model-switch pill.
  final String? modelLabel;

  /// Opens the model picker sheet from the composer pill.
  final VoidCallback? onPickModel;
  final Widget title;
  final String? disabledHint;
  final bool wakeMode;
  final bool waking;
  final VoidCallback? onWake;
  final bool updateMode;
  final bool updating;
  final VoidCallback? onUpdate;
  final VoidCallback? onDismissUpdate;

  @override
  State<ChatScaffold> createState() => _ChatScaffoldState();
}

class _ChatScaffoldState extends State<ChatScaffold> {
  /// Stable key into the composer: dropped files are routed through the
  /// composer's public attach pipeline (same limits/upload/chips as the
  /// attach button).
  final GlobalKey<ChatComposerState> _composerKey =
      GlobalKey<ChatComposerState>();

  /// True while an OS file drag hovers over the chat area.
  bool _dragOver = false;

  // Чек-лист задач выводится из ВСЕЙ истории, а `visibleBlocks` — снимок
  // списка. Считаем только при реальном изменении структуры истории.
  List<ChatChecklistTask> _checklistTasks = const [];
  int? _checklistKey;
  ChatSessionController? _checklistController;

  // Widget-field accessors keep the build body readable.
  ChatSessionController get controller => widget.controller;
  ProdavanApi get api => widget.api;
  bool get chatSendable => widget.chatSendable;
  bool get loading => widget.loading;
  VoidCallback? get onOpenChatSettings => widget.onOpenChatSettings;
  void Function(String sessionId)? get onSessionMaterialized =>
      widget.onSessionMaterialized;
  VoidCallback? get onDraftPresenceChanged => widget.onDraftPresenceChanged;
  String? get modelLabel => widget.modelLabel;
  VoidCallback? get onPickModel => widget.onPickModel;
  String? get disabledHint => widget.disabledHint;
  bool get wakeMode => widget.wakeMode;
  bool get waking => widget.waking;
  VoidCallback? get onWake => widget.onWake;
  bool get updateMode => widget.updateMode;
  bool get updating => widget.updating;
  VoidCallback? get onUpdate => widget.onUpdate;
  VoidCallback? get onDismissUpdate => widget.onDismissUpdate;

  bool get _canAttachDrops =>
      chatSendable && !updateMode && controller.projectId.isNotEmpty;

  void _setDrag(bool value) {
    if (_dragOver != value) {
      setState(() => _dragOver = value);
    }
  }

  /// Чек-лист задач с кэшем по структурной ревизии истории.
  List<ChatChecklistTask> _checklistFor(ChatSessionController controller) {
    final key = Object.hash(
      controller.structureRevision,
      controller.turnInProgress,
      controller.streaming,
    );
    if (key == _checklistKey && identical(controller, _checklistController)) {
      return _checklistTasks;
    }
    _checklistTasks = deriveChecklistTasks(controller.visibleBlocks);
    _checklistKey = key;
    _checklistController = controller;
    return _checklistTasks;
  }

  Future<void> _handleDroppedFiles(DropDoneDetails details) async {
    if (!_canAttachDrops) return;
    final composer = _composerKey.currentState;
    if (composer == null) return;
    final files = <DroppedChatFile>[];
    for (final dropped in details.files) {
      try {
        final bytes = await dropped.readAsBytes();
        if (bytes.isNotEmpty) {
          files.add(DroppedChatFile(name: dropped.name, bytes: bytes));
        }
      } catch (_) {
        // Unreadable drop entries are skipped — the picker stays the
        // reliable path.
      }
    }
    if (files.isEmpty) return;
    await composer.attachDroppedFiles(files);
  }

  double _columnMaxWidth(double width) {
    if (width < 600) return width;
    if (width < 1024) return 768;
    return 900;
  }

  static const double _kChecklistMinWidth = 840;
  static const double _kChatGutter = (AppSpacing.sm + AppSpacing.md) / 2;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final checklistTasks = _checklistFor(controller);
        final showChecklist =
            checklistTasks.isNotEmpty &&
            constraints.maxWidth >= _kChecklistMinWidth;
        final maxW = _columnMaxWidth(constraints.maxWidth);
        final blocks = controller.visibleBlocks;
        final turns = controller.visibleTurns;

        final column = ConstrainedBox(
          constraints: BoxConstraints(maxWidth: maxW),
          child: Column(
            children: [
              Expanded(
                child: DropTarget(
                  onDragDone: _handleDroppedFiles,
                  onDragEntered: (_) => _setDrag(true),
                  onDragExited: (_) => _setDrag(false),
                  child: Stack(
                    children: [
                      Positioned.fill(
                        child: (loading && !controller.hasCachedTranscript)
                            ? const ChatTranscriptSkeleton()
                            : (blocks.isEmpty && !controller.streaming)
                            ? ChatEmptyPlaceholder(highlighted: _dragOver)
                            : ChatTurnList(
                                // Same controller → keep scroll/collapse
                                // state even when the session id is
                                // materialized on first send. A different
                                // controller (chat switch) gets fresh state.
                                key: ValueKey(controller),
                                turns: turns,
                                projectId: controller.projectId,
                                sessionId: controller.sessionId,
                                api: api,
                                hasMoreHistory: controller.hasMoreHistory,
                                loadingHistory: controller.loadingHistory,
                                onLoadOlder: controller.hasMoreHistory
                                    ? controller.loadOlderTranscript
                                    : null,
                                turnStreaming: controller.agentWorking,
                                showWorkingIndicator:
                                    controller.showWorkingIndicator,
                                showReconnectIndicator:
                                    controller.showReconnectIndicator,
                                reconnectAttempt: controller.reconnectAttempt,
                                reconnectMaxAttempts:
                                    controller.reconnectMaxAttempts,
                                reconnectNextModel:
                                    controller.reconnectNextModel,
                                mcpAliases: controller.mcpAliases,
                                costResolver: controller.usageCostUsd,
                                onResolveApproval: (id, decision) =>
                                    controller.resolveApproval(id, decision),
                              ),
                      ),
                      if (_dragOver && _canAttachDrops)
                        const Positioned.fill(
                          child: IgnorePointer(child: ChatDropVeil()),
                        ),
                    ],
                  ),
                ),
              ),
              ChatComposer(
                key: _composerKey,
                projectId: controller.projectId,
                sessionId: controller.sessionId.isEmpty
                    ? null
                    : controller.sessionId,
                api: api,
                draftRestore: controller.interruptedDraft,
                enabled: chatSendable && !updateMode,
                streaming: controller.agentWorking,
                disabledHint: disabledHint,
                wakeMode: wakeMode,
                waking: waking,
                onWake: onWake,
                updateMode: updateMode,
                updating: updating,
                onUpdate: onUpdate,
                onDismissUpdate: onDismissUpdate,
                onSend: (text, refs) =>
                    controller.send(text, attachmentRefs: refs),
                onCancel: controller.agentWorking
                    ? () => controller.cancelStream()
                    : null,
                onOpenSettings: onOpenChatSettings,
                modelLabel: modelLabel,
                onPickModel: onPickModel,
                onSessionMaterialized: (sid) {
                  controller.sessionId = sid;
                  onSessionMaterialized?.call(sid);
                },
                onDraftPresenceChanged: onDraftPresenceChanged,
              ),
            ],
          ),
        );

        if (!showChecklist) {
          return Center(child: column);
        }
        final groupMaxW = maxW + _kChatGutter + kChatChecklistWidth;
        return Center(
          child: ConstrainedBox(
            constraints: BoxConstraints(maxWidth: groupMaxW),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Expanded(child: column),
                const SizedBox(width: _kChatGutter),
                Padding(
                  padding: const EdgeInsets.only(top: AppSpacing.md),
                  child: SizedBox(
                    width: kChatChecklistWidth,
                    child: Align(
                      alignment: Alignment.topLeft,
                      child: ChatChecklistPanel(tasks: checklistTasks),
                    ),
                  ),
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}
