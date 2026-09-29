import 'package:flutter/material.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/chat/models/chat_block.dart';
import 'package:prodavan/core/chat/widgets/blocks/chat_blocks.dart';

String? _nonEmptyString(Object? value) =>
    value is String && value.trim().isNotEmpty ? value : null;

/// ISO timestamp (server `created_at`) → DateTime, null-safe.
DateTime? _parseTimestamp(Object? value) =>
    value is String && value.isNotEmpty ? DateTime.tryParse(value) : null;

int? _intOrNull(Object? value) {
  if (value is int) return value;
  if (value is num) return value.toInt();
  return null;
}

class ChatBlockRenderer extends StatelessWidget {
  const ChatBlockRenderer({
    super.key,
    required this.block,
    this.pairedToolResult,
    this.projectId,
    this.sessionId,
    this.api,
    this.onResolveApproval,
    this.costResolver,
    this.turnStreaming = false,
  });

  final ChatBlock block;
  final ChatBlock? pairedToolResult;
  final String? projectId;
  final String? sessionId;
  final ProdavanApi? api;
  final void Function(String approvalId, String decision)? onResolveApproval;
  final double? Function(String? model, int? inputTokens, int? outputTokens)? costResolver;

  /// Parent turn is still streaming — the subagent block uses it to decide
  /// whether the child agent is running (spinner + transcript polling).
  /// ChatMessageList should forward its own `turnStreaming` here.
  final bool turnStreaming;

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
          timestamp: _parseTimestamp(block.raw['created_at']),
        );
      case 'assistant_markdown':
        final usage = block.raw['usage'];
        return AssistantStreamBlock(
          text: block.text,
          streaming: block.isStreaming,
          cancelled: block.raw['_cancelled'] == true,
          interrupted: block.raw['_interrupted'] == true,
          usageRaw: usage is Map ? Map<String, dynamic>.from(usage) : null,
          costResolver: costResolver,
          completedAt: _parseTimestamp(block.raw['created_at']),
          turnMs: _intOrNull(block.raw['turn_ms']),
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
        // Sidechain transcripts are keyed by the PARENT tool-use id; block.id
        // falls back to agent_id, so prefer parent_tool_use_id explicitly.
        final toolUseId = _nonEmptyString(block.raw['parent_tool_use_id']) ?? block.id;
        final summary = _nonEmptyString(block.raw['result_summary']);
        final eventsRaw = block.raw['events'];
        return SubagentBlock(
          title: _nonEmptyString(block.raw['agent_type']) ??
              _nonEmptyString(block.raw['agent_id']) ??
              'Subagent',
          events: eventsRaw is List ? eventsRaw : const <dynamic>[],
          resultSummary: summary,
          running: turnStreaming && summary == null,
          onFetchSidechain:
              api != null && projectId != null && sessionId != null && toolUseId.isNotEmpty
                  ? () async {
                      final body = await api!.getSidechainTranscript(
                        projectId: projectId!,
                        sessionId: sessionId!,
                        toolUseId: toolUseId,
                      );
                      final items = body['blocks'] ?? body['messages'] ?? body['events'];
                      if (items is List) {
                        return [
                          for (final item in items)
                            if (item is Map<String, dynamic>)
                              item
                            else if (item is Map)
                              Map<String, dynamic>.from(item),
                        ];
                      }
                      return const <Map<String, dynamic>>[];
                    }
                  : null,
        );
      case 'plan':
        return PlanProgressBlock(
          tasks: block.raw['tasks'] as List? ?? const [],
          message: block.raw['message'] as String?,
        );
      case 'usage':
        // Usage is attached to assistant blocks by the projection; a leftover
        // standalone block renders nothing.
        return const SizedBox.shrink();
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
