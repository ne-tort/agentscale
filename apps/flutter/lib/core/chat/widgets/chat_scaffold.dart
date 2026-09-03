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
    ActivityGroupKind.command => l10n.projectChatGroupCommands(count),
    ActivityGroupKind.mcp => l10n.projectChatGroupMcp(count),
  };
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
    this.groupBlocks = true,
  });

  final List<ChatBlock> blocks;
  final String? projectId;
  final String? sessionId;
  final ProdavanApi? api;
  final void Function(String approvalId, String decision)? onResolveApproval;
  final bool hasMoreHistory;
  final bool loadingHistory;
  final VoidCallback? onLoadOlder;
  final bool groupBlocks;

  @override
  State<ChatMessageList> createState() => ChatMessageListState();
}

class ChatMessageListState extends State<ChatMessageList> {
  final _scroll = ScrollController();
  bool _stickToBottom = true;
  int _lastFingerprint = 0;
  int _lastBlockCount = 0;
  double? _anchorExtent;

  @override
  void initState() {
    super.initState();
    _scroll.addListener(_onScroll);
  }

  void _onScroll() {
    if (!_scroll.hasClients) return;
    if (_scroll.position.pixels <= 48 &&
        widget.hasMoreHistory &&
        !widget.loadingHistory &&
        widget.onLoadOlder != null) {
      _anchorExtent = _scroll.position.maxScrollExtent;
      widget.onLoadOlder!();
    }
  }

  Widget _renderPair(({ChatBlock block, ChatBlock? paired}) item) {
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
    final prepended = widget.blocks.length > _lastBlockCount && _anchorExtent != null;
    if (prepended) {
      final anchor = _anchorExtent!;
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (_scroll.hasClients) {
          final delta = _scroll.position.maxScrollExtent - anchor;
          _scroll.jumpTo(_scroll.offset + delta);
        }
        _anchorExtent = null;
      });
    } else if (_stickToBottom && fp != _lastFingerprint) {
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
    final entries = widget.groupBlocks ? groupDisplayEntries(widget.blocks) : [
      for (final item in mergeToolPairs(widget.blocks)) ChatDisplaySingle(item: item),
    ];

    return NotificationListener<ScrollNotification>(
      onNotification: (n) {
        if (n is UserScrollNotification) {
          _stickToBottom = n.metrics.pixels >= n.metrics.maxScrollExtent - 48;
        }
        return false;
      },
      child: ListView.builder(
        controller: _scroll,
        padding: EdgeInsets.all(AppSpacing.md),
        itemCount: entries.length + (widget.loadingHistory ? 1 : 0),
        itemBuilder: (context, index) {
          if (widget.loadingHistory && index == 0) {
            return const Padding(
              padding: EdgeInsets.only(bottom: AppSpacing.sm),
              child: Center(child: SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))),
            );
          }
          final entryIndex = widget.loadingHistory ? index - 1 : index;
          final entry = entries[entryIndex];
          return switch (entry) {
            ChatDisplaySingle(:final item) => _renderPair(item),
            ChatDisplayGroup(:final kind, :final items) => GroupedActivityBlock(
                kind: kind,
                items: items,
                label: _groupLabel(l10n, kind, items.length),
                diffStats: aggregateDiffStats(items),
                childBuilder: _renderPair,
              ),
          };
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
                  child: loading
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
                              groupBlocks: !controller.streaming,
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
