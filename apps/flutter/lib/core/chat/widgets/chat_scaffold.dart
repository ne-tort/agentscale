import 'package:flutter/material.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/chat/controller/chat_session_controller.dart';
import 'package:prodavan/core/chat/models/chat_block.dart';
import 'package:prodavan/core/chat/widgets/blocks/chat_block_renderer.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/features/employee/widgets/chat_composer.dart';

int _blocksScrollFingerprint(List<ChatBlock> blocks) {
  if (blocks.isEmpty) return 0;
  final last = blocks.last;
  return Object.hash(blocks.length, last.kind, last.text.length, last.isStreaming);
}

class ChatMessageList extends StatefulWidget {
  const ChatMessageList({
    super.key,
    required this.blocks,
    this.projectId,
    this.sessionId,
    this.api,
    this.onResolveApproval,
  });

  final List<ChatBlock> blocks;
  final String? projectId;
  final String? sessionId;
  final ProdavanApi? api;
  final void Function(String approvalId, String decision)? onResolveApproval;

  @override
  State<ChatMessageList> createState() => ChatMessageListState();
}

class ChatMessageListState extends State<ChatMessageList> {
  final _scroll = ScrollController();
  bool _stickToBottom = true;
  int _lastFingerprint = 0;

  List<({ChatBlock block, ChatBlock? paired})> _displayBlocks(List<ChatBlock> blocks) {
    final out = <({ChatBlock block, ChatBlock? paired})>[];
    var i = 0;
    while (i < blocks.length) {
      final paired = pairedToolResultFor(blocks, i);
      if (paired != null) {
        out.add((block: blocks[i], paired: paired));
        i += 2;
        continue;
      }
      if (isMergedToolResult(blocks, i)) {
        i++;
        continue;
      }
      out.add((block: blocks[i], paired: null));
      i++;
    }
    return out;
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
    if (_stickToBottom && fp != _lastFingerprint) {
      _lastFingerprint = fp;
      _scrollToBottom();
    }
  }

  @override
  void dispose() {
    _scroll.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    _lastFingerprint = _blocksScrollFingerprint(widget.blocks);
    final display = _displayBlocks(widget.blocks);
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
        itemCount: display.length,
        itemBuilder: (context, index) {
          final item = display[index];
          final block = item.block;
          return ChatBlockRenderer(
            key: ValueKey('${block.kind}-${block.id}-$index-${block.text.length}-${block.isStreaming}'),
            block: block,
            pairedToolResult: item.paired,
            projectId: widget.projectId,
            sessionId: widget.sessionId,
            api: widget.api,
            onResolveApproval: widget.onResolveApproval,
          );
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
    required this.chatAvailable,
    required this.loading,
    required this.onOpenChatSettings,
    required this.title,
  });

  final ChatSessionController controller;
  final ProdavanApi api;
  final bool chatAvailable;
  final bool loading;
  final VoidCallback onOpenChatSettings;
  final Widget title;

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
                      ? const Center(child: CircularProgressIndicator())
                      : controller.visibleBlocks.isEmpty
                          ? const SizedBox.shrink()
                          : ChatMessageList(
                              blocks: controller.visibleBlocks,
                              projectId: controller.projectId,
                              sessionId: controller.sessionId,
                              api: api,
                              onResolveApproval: controller.sessionId == null
                                  ? null
                                  : (id, decision) => controller.resolveApproval(id, decision),
                            ),
                ),
                ChatComposer(
                  projectId: controller.projectId,
                  api: api,
                  enabled: chatAvailable,
                  streaming: controller.streaming,
                  disabledHint: null,
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
