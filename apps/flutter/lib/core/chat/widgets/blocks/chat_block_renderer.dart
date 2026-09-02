import 'package:flutter/material.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/chat/models/chat_block.dart';
import 'package:prodavan/core/chat/widgets/blocks/chat_blocks.dart';

class ChatBlockRenderer extends StatelessWidget {
  const ChatBlockRenderer({
    super.key,
    required this.block,
    this.projectId,
    this.sessionId,
    this.api,
    this.onResolveApproval,
  });

  final ChatBlock block;
  final String? projectId;
  final String? sessionId;
  final ProdavanApi? api;
  final void Function(String approvalId, String decision)? onResolveApproval;

  @override
  Widget build(BuildContext context) {
    switch (block.kind) {
      case 'user':
        final refs = block.raw['attachment_refs'];
        return UserMessageBlock(
          text: block.text,
          attachmentRefs: refs is List ? refs.cast<String>() : const [],
        );
      case 'assistant_markdown':
        return AssistantStreamBlock(
          text: block.text,
          streaming: block.isStreaming,
          cancelled: block.raw['_cancelled'] == true,
        );
      case 'thinking':
        return ThinkingBlock(
          text: block.text,
          durationMs: block.raw['duration_ms'] as int?,
          streaming: block.isStreaming,
        );
      case 'tool_call':
        return ToolCallBlock(
          name: block.raw['name'] as String? ?? 'tool',
          input: block.raw['input'] is Map ? Map<String, dynamic>.from(block.raw['input'] as Map) : const {},
        );
      case 'tool_result':
        return ToolResultBlock(
          name: block.raw['name'] as String? ?? 'tool',
          output: block.raw['output'],
          isError: block.raw['is_error'] == true,
        );
      case 'approval':
        final id = block.raw['id'] as String? ?? '';
        return ApprovalBlock(
          name: block.raw['name'] as String? ?? 'tool',
          approvalId: id,
          input: block.raw['input'] is Map ? Map<String, dynamic>.from(block.raw['input'] as Map) : const {},
          onAllow: onResolveApproval == null ? null : () => onResolveApproval!(id, 'allow'),
          onDeny: onResolveApproval == null ? null : () => onResolveApproval!(id, 'deny'),
        );
      case 'subagent':
        final subId = block.raw['id'] as String? ?? block.raw['agent_id'] as String? ?? '';
        return SubagentBlock(
          title: block.raw['agent_type'] as String? ?? block.raw['agent_id'] as String? ?? 'Subagent',
          events: block.raw['events'] as List? ?? const [],
          onFetchSidechain: api != null && projectId != null && sessionId != null && subId.isNotEmpty
              ? () async {
                  final body = await api!.getSidechainTranscript(
                    projectId: projectId!,
                    sessionId: sessionId!,
                    toolUseId: subId,
                  );
                  final items = body['blocks'] ?? body['messages'] ?? body['events'];
                  if (items is List) return items.cast<Map<String, dynamic>>();
                  return const [];
                }
              : null,
        );
      case 'plan':
        return PlanProgressBlock(
          tasks: block.raw['tasks'] as List? ?? const [],
        );
      case 'usage':
        return UsageBlock(raw: block.raw);
      case 'error':
        return Card(
          color: Theme.of(context).colorScheme.errorContainer,
          child: ListTile(
            title: Text(block.raw['message'] as String? ?? 'Error'),
            subtitle: Text(block.raw['code'] as String? ?? ''),
          ),
        );
      case 'status':
      case 'system_notice':
      case 'tool_progress':
      case 'tool_call_delta':
        return const SizedBox.shrink();
      default:
        return const SizedBox.shrink();
    }
  }
}
