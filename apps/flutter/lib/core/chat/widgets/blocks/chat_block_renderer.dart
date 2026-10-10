import 'package:flutter/material.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/chat/models/chat_block.dart';
import 'package:prodavan/core/chat/tool_activity_labels.dart';
import 'package:prodavan/core/chat/widgets/blocks/chat_blocks.dart';
import 'package:prodavan/l10n/app_localizations.dart';

String? _nonEmptyString(Object? value) =>
    value is String && value.trim().isNotEmpty ? value : null;

/// Header title for a subagent block: prefer the model (the useful signal),
/// then the task, then a generic label. The runtime's "default" profile name
/// is meaningless to the user and is never shown.
String _subagentTitle({String? agentType, String? model, String? task}) {
  if (model != null) return model;
  final type = agentType?.toLowerCase();
  if (type != null && type != 'default') return agentType!;
  if (task != null) {
    final flat = task.replaceAll('\n', ' ').trim();
    return flat.length <= 60 ? flat : '${flat.substring(0, 60)}…';
  }
  return 'Subagent';
}

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
    this.mcpAliases = const {},
    this.turnStreaming = false,
  });

  final ChatBlock block;
  final ChatBlock? pairedToolResult;
  final String? projectId;
  final String? sessionId;
  final ProdavanApi? api;
  final void Function(String approvalId, String decision)? onResolveApproval;
  final double? Function(String? model, int? inputTokens, int? outputTokens)? costResolver;

  /// MCP tool display aliases (see ChatSessionController.mcpAliases) —
  /// forwarded to the tool activity labels.
  final Map<String, String> mcpAliases;

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
          imageLoader: api != null && projectId != null
              ? (attachmentId) =>
                  api!.downloadProjectAttachmentBytes(projectId: projectId!, attachmentId: attachmentId)
              : null,
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
        // `agent.spawn` is rendered as a dedicated subagent block (with model,
        // task, transcript) — the raw tool-call line would duplicate it as a
        // noisy "Субагент agent.spawn" row. Suppress it here.
        if ((block.raw['name'] as String?)?.trim() == 'agent.spawn') {
          return const SizedBox.shrink();
        }
        if (pairedToolResult != null && pairedToolResult!.kind == 'tool_result') {
          final input = block.raw['input'] is Map
              ? Map<String, dynamic>.from(block.raw['input'] as Map)
              : const <String, dynamic>{};
          final pairedOutput = pairedToolResult!.raw['output'];
          return ToolActivityBlock(
            name: block.raw['name'] as String? ?? pairedToolResult!.raw['name'] as String? ?? 'tool',
            input: input,
            output: pairedOutput,
            isError: pairedToolResult!.raw['is_error'] == true || mcpToolFailed(pairedOutput),
            mcpAliases: mcpAliases,
          );
        }
        return ToolCallBlock(
          name: block.raw['name'] as String? ?? 'tool',
          input: block.raw['input'] is Map ? Map<String, dynamic>.from(block.raw['input'] as Map) : const {},
          mcpAliases: mcpAliases,
        );
      case 'tool_result':
        final rawOutput = block.raw['output'];
        return ToolResultBlock(
          name: block.raw['name'] as String? ?? 'tool',
          output: rawOutput,
          isError: block.raw['is_error'] == true || mcpToolFailed(rawOutput),
          mcpAliases: mcpAliases,
        );
      case 'approval':
        final id = block.raw['id'] as String? ?? '';
        return ApprovalBlock(
          name: block.raw['name'] as String? ?? 'tool',
          approvalId: id,
          input: block.raw['input'] is Map ? Map<String, dynamic>.from(block.raw['input'] as Map) : const {},
          mcpAliases: mcpAliases,
          onAllow: onResolveApproval == null ? null : () => onResolveApproval!(id, 'allow'),
          onDeny: onResolveApproval == null ? null : () => onResolveApproval!(id, 'deny'),
        );
      case 'subagent':
        // Sidechain transcripts are keyed by the PARENT tool-use id; block.id
        // falls back to agent_id, so prefer parent_tool_use_id explicitly.
        final toolUseId = _nonEmptyString(block.raw['parent_tool_use_id']) ?? block.id;
        final summary = _nonEmptyString(block.raw['result_summary']);
        final eventsRaw = block.raw['events'];
        // The agent_type default ("default") is a runtime profile name, not a
        // useful label — prefer the model, then the task, then a generic title.
        final agentType = _nonEmptyString(block.raw['agent_type']);
        final model = _nonEmptyString(block.raw['model']);
        final task = _nonEmptyString(block.raw['task']);
        final title = _subagentTitle(agentType: agentType, model: model, task: task);
        // "Running" is decided by THIS subagent's own lifecycle, not the parent
        // turn: a stop event (summary or status=completed) ends it even while
        // the parent keeps streaming.
        final stopped = summary != null || _nonEmptyString(block.raw['status']) == 'completed';
        return SubagentBlock(
          title: title,
          model: model,
          task: task,
          events: eventsRaw is List ? eventsRaw : const <dynamic>[],
          resultSummary: summary,
          usageRaw: block.raw['usage'] is Map
              ? Map<String, dynamic>.from(block.raw['usage'] as Map)
              : null,
          turnMs: _intOrNull(block.raw['turn_ms']),
          completedAt: _parseTimestamp(block.raw['completed_at'] ?? block.raw['created_at']),
          running: turnStreaming && !stopped,
          onFetchSidechain:
              api != null && projectId != null && sessionId != null && toolUseId.isNotEmpty
                  ? () async {
                      final body = await api!.getSidechainTranscript(
                        projectId: projectId!,
                        sessionId: sessionId!,
                        toolUseId: toolUseId,
                      );
                      final items = body['blocks'] ?? body['entries'] ?? body['messages'];
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
      case 'permission_denial':
        // Tool denied by policy/user — surfaced as a muted system line
        // (previously invisible end-to-end: block kind existed, no case).
        final l10n = AppLocalizations.of(context);
        final rawName = block.raw['name'] as String? ?? block.raw['tool'] as String? ?? '';
        final presentation = formatToolActivityLabel(
          l10n,
          name: rawName.isEmpty ? 'tool' : rawName,
          mcpAliases: mcpAliases,
        );
        return ChatMutedLine(
          label: '${l10n.projectChatPermissionDenied}: ${presentation.label}',
        );
      case 'system_notice':
        // Платформенное уведомление. Видимая причина сейчас одна — исчерпан
        // лимит шагов: без подписи пользователь видит просто оборванный ответ
        // и не понимает, что делать дальше. Прочие причины (compact_boundary)
        // остаются невидимыми, как и раньше.
        final noticeReason = block.raw['reason'] as String? ?? '';
        if (noticeReason == 'max_turns') {
          return ChatMutedLine(
            label: AppLocalizations.of(context).projectChatMaxTurnsReached,
          );
        }
        return const SizedBox.shrink();
      case 'status':
      case 'tool_progress':
      case 'tool_call_delta':
        return const SizedBox.shrink();
      default:
        return const SizedBox.shrink();
    }
  }
}
