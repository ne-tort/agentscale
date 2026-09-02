import 'package:flutter/material.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/chat/controller/chat_session_controller.dart';
import 'package:prodavan/core/chat/models/chat_block.dart';
import 'package:prodavan/core/chat/widgets/blocks/chat_block_renderer.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/features/employee/widgets/chat_composer.dart';
import 'package:prodavan/l10n/app_localizations.dart';

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

  @override
  void didUpdateWidget(covariant ChatMessageList oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (_stickToBottom && widget.blocks.length != oldWidget.blocks.length) {
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
  }

  @override
  void dispose() {
    _scroll.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
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
        itemCount: widget.blocks.length,
        itemBuilder: (context, index) {
          return ChatBlockRenderer(
            block: widget.blocks[index],
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
    required this.onOpenSettings,
    required this.onOpenApproval,
    required this.title,
  });

  final ChatSessionController controller;
  final ProdavanApi api;
  final bool chatAvailable;
  final bool loading;
  final VoidCallback onOpenSettings;
  final void Function(Map<String, dynamic> approval) onOpenApproval;
  final Widget title;

  double _columnMaxWidth(double width) {
    if (width < 600) return width;
    if (width < 1024) return 768;
    return 900;
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return LayoutBuilder(
      builder: (context, constraints) {
        final maxW = _columnMaxWidth(constraints.maxWidth);
        return Center(
          child: ConstrainedBox(
            constraints: BoxConstraints(maxWidth: maxW),
            child: Column(
              children: [
                if (controller.pendingApprovals.isNotEmpty)
                  MaterialBanner(
                    content: Text('${l10n.projectToolApprovalHint} (${controller.pendingApprovals.length})'),
                    actions: [
                      TextButton(
                        onPressed: () => onOpenApproval(controller.pendingApprovals.first),
                        child: Text(l10n.projectApproveTool),
                      ),
                    ],
                  ),
                if (controller.availableModels.isNotEmpty)
                  Padding(
                    padding: EdgeInsets.fromLTRB(AppSpacing.md, AppSpacing.sm, AppSpacing.md, 0),
                    child: InputDecorator(
                      decoration: InputDecoration(
                        labelText: l10n.projectChatModelLabel,
                        border: const OutlineInputBorder(),
                        contentPadding: EdgeInsets.symmetric(horizontal: AppSpacing.md, vertical: AppSpacing.xs),
                      ),
                      child: DropdownButtonHideUnderline(
                        child: DropdownButton<String>(
                          isExpanded: true,
                          value: controller.selectedModel ?? controller.defaultModel,
                          items: controller.availableModels
                              .map((m) {
                                final id = m['id'] as String? ?? m['label'] as String? ?? '';
                                return DropdownMenuItem<String>(
                                  value: id,
                                  child: Text(m['label'] as String? ?? id),
                                );
                              })
                              .where((item) => item.value != null && item.value!.isNotEmpty)
                              .toList(),
                          onChanged: controller.streaming
                              ? null
                              : (v) {
                                  controller.selectedModel = v;
                                  controller.notifyImmediate();
                                },
                        ),
                      ),
                    ),
                  ),
                Expanded(
                  child: loading
                      ? const Center(child: CircularProgressIndicator())
                      : controller.visibleBlocks.isEmpty
                          ? Center(child: Text(l10n.projectEmptyChatHint))
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
                  enabled: chatAvailable && !controller.streaming,
                  disabledHint: l10n.errorPodNotRunning,
                  onSend: (text, refs) => controller.send(text, attachmentRefs: refs),
                  onCancel: controller.streaming ? () => controller.cancelStream() : null,
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}
