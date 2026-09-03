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

const _kEstimatedRowHeight = 48.0;
const _kScrollCacheExtent = 700.0;
const _kStickThreshold = 48.0;

int _blocksScrollFingerprint(List<ChatBlock> blocks) {
  if (blocks.isEmpty) return 0;
  final last = blocks.last;
  return Object.hash(blocks.length, last.kind, last.text.length, last.isStreaming);
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
    this.totalEvents,
    this.oldestSeq,
    this.newestSeq,
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
  final int? totalEvents;
  final int? oldestSeq;
  final int? newestSeq;

  @override
  State<ChatMessageList> createState() => ChatMessageListState();
}

class ChatMessageListState extends State<ChatMessageList> {
  final _scroll = ScrollController();
  int _lastFingerprint = 0;
  int _lastBlockCount = 0;
  double? _anchorPixels;
  double? _anchorMaxExtent;

  @override
  void initState() {
    super.initState();
    _scroll.addListener(_onScroll);
  }

  bool _isAtBottom([ScrollMetrics? metrics]) {
    final m = metrics ?? (_scroll.hasClients ? _scroll.position : null);
    if (m == null) return true;
    return m.pixels >= m.maxScrollExtent - _kStickThreshold;
  }

  double _estimatedTopExtentFor({
    required bool hasMoreHistory,
    int? totalEvents,
    int? oldestSeq,
    int? newestSeq,
  }) {
    if (!hasMoreHistory || totalEvents == null || oldestSeq == null) {
      return 0;
    }
    final loaded = newestSeq != null ? newestSeq - oldestSeq + 1 : 0;
    final unloaded = (totalEvents - loaded).clamp(0, totalEvents);
    return unloaded * _kEstimatedRowHeight;
  }

  double _estimatedTopExtent() => _estimatedTopExtentFor(
        hasMoreHistory: widget.hasMoreHistory,
        totalEvents: widget.totalEvents,
        oldestSeq: widget.oldestSeq,
        newestSeq: widget.newestSeq,
      );

  void _restoreScrollAnchor(double anchor, double oldMax) {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scroll.hasClients) {
        final extentDelta = _scroll.position.maxScrollExtent - oldMax;
        _scroll.jumpTo(anchor + extentDelta);
      }
    });
  }

  void _captureScrollAnchor() {
    if (!_scroll.hasClients) return;
    _anchorPixels = _scroll.offset;
    _anchorMaxExtent = _scroll.position.maxScrollExtent;
  }

  void _onScroll() {
    if (!_scroll.hasClients) return;
    if (_scroll.position.pixels <= _estimatedTopExtent() + _kStickThreshold &&
        widget.hasMoreHistory &&
        !widget.loadingHistory &&
        widget.onLoadOlder != null) {
      _captureScrollAnchor();
      widget.onLoadOlder!();
    }
  }

  Widget _renderPair(ChatDisplayPair item) {
    final block = item.block;
    return ChatBlockRenderer(
      key: ValueKey('${block.kind}-${block.id}-${block.text.length}-${block.isStreaming}'),
      block: block,
      pairedToolResult: item.paired,
      projectId: widget.projectId,
      sessionId: widget.sessionId,
      api: widget.api,
      onResolveApproval: widget.onResolveApproval,
    );
  }

  Widget _renderEntry(AppLocalizations l10n, ChatDisplayEntry entry) {
    return switch (entry) {
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
              : l10n.projectChatWorked(items.length),
          items: items,
          innerEntries: groupInnerWorkItems(items),
          diffStats: aggregateDiffStats(items),
          childBuilder: _renderPair,
        ),
    };
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scroll.hasClients) {
        _scroll.animateTo(
          _scroll.position.maxScrollExtent,
          duration: const Duration(milliseconds: 120),
          curve: Curves.easeOut,
        );
      }
    });
  }

  @override
  void didUpdateWidget(covariant ChatMessageList oldWidget) {
    super.didUpdateWidget(oldWidget);
    final fp = _blocksScrollFingerprint(widget.blocks);
    final prepended = widget.blocks.length > _lastBlockCount && _anchorPixels != null;
    final topSpacerChanged = _estimatedTopExtentFor(
          hasMoreHistory: oldWidget.hasMoreHistory,
          totalEvents: oldWidget.totalEvents,
          oldestSeq: oldWidget.oldestSeq,
          newestSeq: oldWidget.newestSeq,
        ) !=
        _estimatedTopExtent();
    final metaChanged = oldWidget.loadingHistory != widget.loadingHistory || topSpacerChanged;

    if (!oldWidget.loadingHistory && widget.loadingHistory && _scroll.hasClients) {
      _captureScrollAnchor();
    }

    if (prepended) {
      final anchor = _anchorPixels!;
      final oldMax = _anchorMaxExtent ?? anchor;
      _restoreScrollAnchor(anchor, oldMax);
      _anchorPixels = null;
      _anchorMaxExtent = null;
    } else if (metaChanged && _scroll.hasClients && !_isAtBottom()) {
      final anchor = _scroll.offset;
      final oldMax = _scroll.position.maxScrollExtent;
      _restoreScrollAnchor(anchor, oldMax);
    } else if (fp != _lastFingerprint && _isAtBottom()) {
      _lastFingerprint = fp;
      _scrollToBottom();
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
    _lastFingerprint = _blocksScrollFingerprint(widget.blocks);
    _lastBlockCount = widget.blocks.length;
    final l10n = AppLocalizations.of(context);
    final entries = groupDisplayEntries(widget.blocks, turnStreaming: widget.turnStreaming);
    final topSpacer = _estimatedTopExtent();
    final headerCount = (topSpacer > 0 ? 1 : 0) + (widget.loadingHistory ? 1 : 0);

    return NotificationListener<ScrollNotification>(
      onNotification: (_) => false,
      child: ListView.builder(
        controller: _scroll,
        padding: EdgeInsets.all(AppSpacing.md),
        cacheExtent: _kScrollCacheExtent,
        itemCount: headerCount + entries.length,
        itemBuilder: (context, index) {
          var cursor = index;
          if (topSpacer > 0) {
            if (cursor == 0) return SizedBox(height: topSpacer);
            cursor--;
          }
          if (widget.loadingHistory) {
            if (cursor == 0) {
              return const Padding(
                padding: EdgeInsets.only(bottom: AppSpacing.sm),
                child: Center(
                  child: SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2)),
                ),
              );
            }
            cursor--;
          }
          return _renderEntry(l10n, entries[cursor]);
        },
      ),
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
                              onLoadOlder: controller.hasMoreHistory ? controller.loadOlderTranscript : null,
                              turnStreaming: controller.streaming,
                              totalEvents: controller.totalEvents,
                              oldestSeq: controller.oldestSeq,
                              newestSeq: controller.newestSeq,
                              onResolveApproval: controller.sessionId == null
                                  ? null
                                  : (id, decision) => controller.resolveApproval(id, decision),
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
