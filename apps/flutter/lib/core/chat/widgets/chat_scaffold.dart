import 'package:flutter/material.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/chat/controller/chat_session_controller.dart';
import 'package:prodavan/core/chat/models/chat_block.dart';
import 'package:prodavan/core/chat/widgets/blocks/chat_block_renderer.dart';
import 'package:prodavan/core/chat/widgets/blocks/chat_blocks.dart';
import 'package:prodavan/core/chat/widgets/chat_display_grouping.dart';
import 'package:prodavan/core/chat/widgets/chat_transcript_skeleton.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/features/employee/widgets/chat_composer.dart';
import 'package:prodavan/l10n/app_localizations.dart';

const _kStickThreshold = 48.0;

int _blocksScrollFingerprint(List<ChatBlock> blocks) {
  if (blocks.isEmpty) return 0;
  final last = blocks.last;
  return Object.hash(blocks.length, last.kind, last.text.length, last.isStreaming);
}

/// Stable across stream text growth (prefix-based); prefers explicit tool/call ids.
String _stableBlockKey(ChatBlock block) {
  if (block.id.isNotEmpty) return '${block.kind}-${block.id}';
  final t = block.text;
  final prefix = t.length <= 64 ? t : t.substring(0, 64);
  return '${block.kind}-${Object.hash(prefix, block.raw['duration_ms'], block.raw['name'])}';
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

class _WorkSessionBlock extends StatefulWidget {
  const _WorkSessionBlock({
    required this.label,
    required this.items,
    required this.innerEntries,
    required this.childBuilder,
    this.diffStats,
  });

  final String label;
  final List<ChatDisplayPair> items;
  final List<ChatDisplayEntry> innerEntries;
  final Widget Function(ChatDisplayPair item) childBuilder;
  final ({int added, int removed})? diffStats;

  @override
  State<_WorkSessionBlock> createState() => _WorkSessionBlockState();
}

class _WorkSessionBlockState extends State<_WorkSessionBlock> {
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
            Text('+${stats.added}', style: TextStyle(color: scheme.primary, fontSize: 12)),
          if (stats.added > 0 && stats.removed > 0) const SizedBox(width: 4),
          if (stats.removed > 0)
            Text('-${stats.removed}', style: TextStyle(color: scheme.error, fontSize: 12)),
        ],
      );
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        ChatMutedLine(
          label: widget.label,
          trailing: badge,
          expanded: _open,
          onTap: () => setState(() => _open = !_open),
        ),
        if (_open)
          ChatInsetPanel(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                for (final inner in widget.innerEntries)
                  switch (inner) {
                    ChatDisplaySingle(:final item) => widget.childBuilder(item),
                    ChatDisplayGroup(:final kind, :final items) => GroupedActivityBlock(
                        kind: kind,
                        items: items,
                        label: _groupLabel(l10n, kind, items.length),
                        diffStats: aggregateDiffStats(items),
                        childBuilder: widget.childBuilder,
                      ),
                    ChatDisplayWorkSession() => const SizedBox.shrink(),
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

  @override
  State<ChatMessageList> createState() => ChatMessageListState();
}

class ChatMessageListState extends State<ChatMessageList> {
  final _scroll = ScrollController();
  bool _pinnedToBottom = true;
  bool _didInitialJump = false;
  bool _loadingOlder = false;
  int _lastFingerprint = 0;
  int _lastBlockCount = 0;
  double? _anchorPixels;
  double? _anchorMaxExtent;

  @override
  void initState() {
    super.initState();
    _scroll.addListener(_onScroll);
    _lastFingerprint = _blocksScrollFingerprint(widget.blocks);
    _lastBlockCount = widget.blocks.length;
  }

  /// Chronological ListView — visual bottom is maxScrollExtent.
  bool _isPinnedToBottom([ScrollMetrics? metrics]) {
    final m = metrics ?? (_scroll.hasClients ? _scroll.position : null);
    if (m == null) return true;
    return m.pixels >= m.maxScrollExtent - _kStickThreshold;
  }

  void _captureAnchor() {
    if (!_scroll.hasClients) return;
    _anchorPixels = _scroll.offset;
    _anchorMaxExtent = _scroll.position.maxScrollExtent;
  }

  void _restoreAnchor() {
    final anchor = _anchorPixels;
    final oldMax = _anchorMaxExtent;
    if (anchor == null || oldMax == null) return;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scroll.hasClients) return;
      final delta = _scroll.position.maxScrollExtent - oldMax;
      _scroll.jumpTo((anchor + delta).clamp(0.0, _scroll.position.maxScrollExtent));
      _anchorPixels = null;
      _anchorMaxExtent = null;
      _loadingOlder = false;
      _maybeAutoloadOlder();
    });
  }

  void _scrollToBottom({bool animate = false}) {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scroll.hasClients) return;
      final target = _scroll.position.maxScrollExtent;
      if (animate) {
        _scroll.animateTo(
          target,
          duration: const Duration(milliseconds: 120),
          curve: Curves.easeOut,
        );
      } else {
        _scroll.jumpTo(target);
      }
      // Extent often grows after first layout of variable-height blocks.
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (!_scroll.hasClients) return;
        final t2 = _scroll.position.maxScrollExtent;
        if ((t2 - _scroll.offset).abs() > 1) {
          _scroll.jumpTo(t2);
        }
        _maybeAutoloadOlder();
      });
    });
  }

  void _maybeAutoloadOlder() {
    if (!_scroll.hasClients) return;
    if (!widget.hasMoreHistory ||
        widget.loadingHistory ||
        _loadingOlder ||
        widget.onLoadOlder == null) {
      return;
    }
    if (_scroll.position.maxScrollExtent < _kStickThreshold) {
      _loadingOlder = true;
      _captureAnchor();
      widget.onLoadOlder!();
    }
  }

  void _onScroll() {
    if (!_scroll.hasClients) return;
    final pos = _scroll.position;
    _pinnedToBottom = _isPinnedToBottom(pos);
    if (pos.pixels <= _kStickThreshold &&
        widget.hasMoreHistory &&
        !widget.loadingHistory &&
        !_loadingOlder &&
        widget.onLoadOlder != null) {
      _loadingOlder = true;
      _captureAnchor();
      widget.onLoadOlder!();
    }
  }

  Widget _renderPair(ChatDisplayPair item) {
    final block = item.block;
    return ChatBlockRenderer(
      key: ValueKey(_stableBlockKey(block)),
      block: block,
      pairedToolResult: item.paired,
      projectId: widget.projectId,
      sessionId: widget.sessionId,
      api: widget.api,
      onResolveApproval: widget.onResolveApproval,
    );
  }

  Widget _renderEntry(AppLocalizations l10n, ChatDisplayEntry entry) {
    // Per-item SelectionArea: wrapping the whole ListView eats drag gestures on first paint.
    return SelectionArea(
      child: switch (entry) {
        ChatDisplaySingle(:final item) => _renderPair(item),
        ChatDisplayGroup(:final kind, :final items) => GroupedActivityBlock(
            kind: kind,
            items: items,
            label: _groupLabel(l10n, kind, items.length),
            diffStats: aggregateDiffStats(items),
            childBuilder: _renderPair,
          ),
        ChatDisplayWorkSession(:final items, :final streaming) => _WorkSessionBlock(
            label: streaming
                ? l10n.projectChatWorking
                : l10n.projectChatWorked(workActionCount(items)),
            items: items,
            innerEntries: groupInnerWorkItems(items),
            diffStats: aggregateDiffStats(items),
            childBuilder: _renderPair,
          ),
      },
    );
  }

  @override
  void didUpdateWidget(covariant ChatMessageList oldWidget) {
    super.didUpdateWidget(oldWidget);
    final fp = _blocksScrollFingerprint(widget.blocks);
    final countGrew = widget.blocks.length > _lastBlockCount;
    final prepended = countGrew &&
        (widget.loadingHistory ||
            oldWidget.loadingHistory ||
            _anchorPixels != null ||
            (widget.blocks.isNotEmpty &&
                oldWidget.blocks.isNotEmpty &&
                widget.blocks.first.id != oldWidget.blocks.first.id &&
                widget.blocks.last.id == oldWidget.blocks.last.id));

    if (!oldWidget.loadingHistory && widget.loadingHistory) {
      _captureAnchor();
    }

    if (prepended) {
      _lastFingerprint = fp;
      _lastBlockCount = widget.blocks.length;
      _restoreAnchor();
      return;
    }

    if (oldWidget.loadingHistory && !widget.loadingHistory) {
      _loadingOlder = false;
      _lastFingerprint = fp;
      _lastBlockCount = widget.blocks.length;
      WidgetsBinding.instance.addPostFrameCallback((_) => _maybeAutoloadOlder());
      return;
    }

    if (fp != _lastFingerprint) {
      final wasPinned = _scroll.hasClients ? _isPinnedToBottom() : _pinnedToBottom;
      _lastFingerprint = fp;
      _lastBlockCount = widget.blocks.length;
      _pinnedToBottom = wasPinned;
      if (wasPinned) {
        _scrollToBottom(animate: _didInitialJump);
      }
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

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final entries = groupDisplayEntries(widget.blocks, turnStreaming: widget.turnStreaming);

    if (!_didInitialJump && widget.blocks.isNotEmpty) {
      _didInitialJump = true;
      _scrollToBottom();
    }

    return Stack(
      children: [
        ListView.builder(
          controller: _scroll,
          padding: EdgeInsets.all(AppSpacing.md),
          itemCount: entries.length,
          itemBuilder: (context, index) => _renderEntry(l10n, entries[index]),
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
    );
  }
}

class ChatScaffold extends StatelessWidget {
  const ChatScaffold({
    super.key,
    required this.controller,
    required this.api,
    required this.chatSendable,
    required this.loading,
    required this.onOpenChatSettings,
    required this.title,
    this.disabledHint,
  });

  final ChatSessionController controller;
  final ProdavanApi api;
  final bool chatSendable;
  final bool loading;
  final VoidCallback onOpenChatSettings;
  final Widget title;
  final String? disabledHint;

  double _columnMaxWidth(double width) {
    if (width < 600) return width;
    if (width < 1024) return 768;
    return 900;
  }

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final maxW = _columnMaxWidth(constraints.maxWidth);
        return Center(
          child: ConstrainedBox(
            constraints: BoxConstraints(maxWidth: maxW),
            child: Column(
              children: [
                Expanded(
                  child: (loading && !controller.hasCachedTranscript)
                      ? const ChatTranscriptSkeleton()
                      : controller.visibleBlocks.isEmpty
                          ? const SizedBox.shrink()
                          : ChatMessageList(
                              blocks: controller.visibleBlocks,
                              projectId: controller.projectId,
                              sessionId: controller.sessionId,
                              api: api,
                              hasMoreHistory: controller.hasMoreHistory,
                              loadingHistory: controller.loadingHistory,
                              onLoadOlder: controller.hasMoreHistory
                                  ? controller.loadOlderTranscript
                                  : null,
                              turnStreaming: controller.streaming,
                              onResolveApproval: (id, decision) =>
                                  controller.resolveApproval(id, decision),
                            ),
                ),
                ChatComposer(
                  projectId: controller.projectId,
                  api: api,
                  enabled: chatSendable,
                  streaming: controller.streaming,
                  disabledHint: disabledHint,
                  onSend: (text, refs) => controller.send(text, attachmentRefs: refs),
                  onCancel: controller.streaming ? () => controller.cancelStream() : null,
                  onOpenSettings: onOpenChatSettings,
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}
