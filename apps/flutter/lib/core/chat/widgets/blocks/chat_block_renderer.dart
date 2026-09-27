import 'package:flutter/material.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/chat/models/chat_block.dart';
import 'package:prodavan/core/chat/widgets/blocks/chat_blocks.dart';

class ChatBlockRenderer extends StatelessWidget {
  const ChatBlockRenderer({
    super.key,
    required this.block,
    this.pairedToolResult,
    this.projectId,
    this.sessionId,
    this.api,
    this.onResolveApproval,
  });

  final ChatBlock block;
  final ChatBlock? pairedToolResult;
  final String? projectId;
  final String? sessionId;
  final ProdavanApi? api;
  final void Function(String approvalId, String decision)? onResolveApproval;

  @override
  Widget build(BuildContext context) {
    switch (block.kind) {
      case 'user':
        final refs = block.raw['attachment_refs'];
        final attachmentsRaw = block.raw['attachments'];
        final attachments = <Map<String, dynamic>>[];
        if (attachmentsRaw is List) {
          for (final item in attachmentsRaw) {
            if (item is Map<String, dynamic>) {
              attachments.add(item);
            } else if (item is Map) {
              attachments.add(Map<String, dynamic>.from(item));
            }
          }
        }
        return UserMessageBlock(
          text: block.text,
          attachmentRefs: refs is List ? refs.cast<String>() : const [],
          attachments: attachments,
        );
      case 'assistant_markdown':
        return AssistantStreamBlock(
          text: block.text,
          streaming: block.isStreaming,
          cancelled: block.raw['_cancelled'] == true,
          interrupted: block.raw['_interrupted'] == true,
        );
      case 'thinking':
        return ThinkingBlock(
          text: block.text,
          durationMs: block.raw['duration_ms'] as int?,
          streaming: block.isStreaming,
        );
      case 'tool_call':
        if (pairedToolResult != null && pairedToolResult!.kind == 'tool_result') {
          final input = block.raw['input'] is Map
              ? Map<String, dynamic>.from(block.raw['input'] as Map)
              : const <String, dynamic>{};
          return ToolActivityBlock(
            name: block.raw['name'] as String? ?? pairedToolResult!.raw['name'] as String? ?? 'tool',
            input: input,
            output: pairedToolResult!.raw['output'],
            isError: pairedToolResult!.raw['is_error'] == true,
          );
        }
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
        return Padding(
          padding: const EdgeInsets.symmetric(vertical: 4),
          child: ChatInsetPanel(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  block.raw['message'] as String? ?? 'Error',
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
                if (block.raw['code'] != null)
                  Text('${block.raw['code']}', style: Theme.of(context).textTheme.bodySmall),
              ],
            ),
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

ChatBlock? pairedToolResultFor(List<ChatBlock> blocks, int index) {
  final block = blocks[index];
  if (block.kind != 'tool_call') return null;
  final callId = block.raw['id'];

  if (index + 1 < blocks.length) {
    final next = blocks[index + 1];
    if (next.kind == 'tool_result') {
      final resultId = next.raw['id'];
      if (callId != null && resultId != null && callId == resultId) return next;
      if (callId == null && resultId == null) return next;
    }
  }

  if (callId == null) return null;
  final limit = blocks.length < index + 25 ? blocks.length : index + 25;
  for (var j = index + 1; j < limit; j++) {
    final b = blocks[j];
    if (b.kind == 'tool_result' && b.raw['id'] == callId) return b;
  }
  return null;
}

bool isMergedToolResult(List<ChatBlock> blocks, int index) {
  if (index <= 0) return false;
  final block = blocks[index];
  if (block.kind != 'tool_result') return false;
  final resultId = block.raw['id'];
  for (var i = 0; i < index; i++) {
    if (blocks[i].kind != 'tool_call') continue;
    final paired = pairedToolResultFor(blocks, i);
    if (paired == null) continue;
    if (identical(paired, block)) return true;
    if (resultId != null && paired.raw['id'] == resultId) return true;
  }
  return false;
}
