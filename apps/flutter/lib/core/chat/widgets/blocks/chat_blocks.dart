import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:markdown/markdown.dart' as md;

import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/l10n/app_localizations.dart';

TextStyle _mutedTextStyle(BuildContext context) {
  final base = Theme.of(context).textTheme.bodyMedium ?? const TextStyle();
  return base.copyWith(
    color: Theme.of(context).colorScheme.onSurfaceVariant.withValues(alpha: 0.72),
    decoration: TextDecoration.underline,
    decorationColor: Theme.of(context).colorScheme.onSurfaceVariant.withValues(alpha: 0.45),
  );
}

TextStyle _mutedBodyStyle(BuildContext context) {
  final base = Theme.of(context).textTheme.bodyMedium ?? const TextStyle();
  return base.copyWith(
    color: Theme.of(context).colorScheme.onSurfaceVariant.withValues(alpha: 0.72),
  );
}

class ChatMutedLine extends StatelessWidget {
  const ChatMutedLine({
    super.key,
    required this.label,
    this.trailing,
    this.expanded = false,
    this.onTap,
  });

  final String label;
  final Widget? trailing;
  final bool expanded;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: EdgeInsets.symmetric(vertical: AppSpacing.xs / 2),
        child: Row(
          children: [
            Expanded(
              child: Text(label, style: _mutedTextStyle(context)),
            ),
            if (trailing != null) trailing!,
          ],
        ),
      ),
    );
  }
}

class ChatInsetPanel extends StatelessWidget {
  const ChatInsetPanel({super.key, required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      width: double.infinity,
      margin: EdgeInsets.only(top: AppSpacing.xs / 2),
      padding: EdgeInsets.all(AppSpacing.sm),
      decoration: BoxDecoration(
        border: Border.all(color: scheme.outlineVariant),
        borderRadius: BorderRadius.circular(8),
        color: scheme.surface,
      ),
      child: child,
    );
  }
}

class ChatMarkdownBody extends StatelessWidget {
  const ChatMarkdownBody({super.key, required this.text, this.selectable = true});

  final String text;
  final bool selectable;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    if (text.isEmpty) return const SizedBox.shrink();
    return MarkdownBody(
      data: text,
      selectable: selectable,
      extensionSet: md.ExtensionSet.gitHubWeb,
      styleSheet: MarkdownStyleSheet.fromTheme(Theme.of(context)).copyWith(
        p: Theme.of(context).textTheme.bodyMedium,
        code: TextStyle(
          fontFamily: 'monospace',
          backgroundColor: scheme.surfaceContainerHighest,
        ),
        codeblockDecoration: BoxDecoration(
          color: scheme.surfaceContainerHighest,
          borderRadius: BorderRadius.circular(8),
        ),
      ),
    );
  }
}

class AssistantStreamBlock extends StatelessWidget {
  const AssistantStreamBlock({super.key, required this.text, this.streaming = false, this.cancelled = false});

  final String text;
  final bool streaming;
  final bool cancelled;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    if (text.isEmpty && !cancelled) return const SizedBox.shrink();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (streaming)
          SelectableText(text, style: Theme.of(context).textTheme.bodyMedium)
        else if (text.isNotEmpty)
          ChatMarkdownBody(text: text),
        if (cancelled)
          Padding(
            padding: EdgeInsets.only(top: AppSpacing.xs),
            child: Text(l10n.projectChatCancelled, style: Theme.of(context).textTheme.labelSmall),
          ),
      ],
    );
  }
}

class UserMessageBlock extends StatelessWidget {
  const UserMessageBlock({super.key, required this.text, this.attachmentRefs = const []});

  final String text;
  final List<String> attachmentRefs;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Align(
      alignment: Alignment.centerRight,
      child: Container(
        margin: EdgeInsets.only(bottom: AppSpacing.sm),
        padding: EdgeInsets.symmetric(horizontal: AppSpacing.md, vertical: AppSpacing.sm),
        constraints: BoxConstraints(maxWidth: MediaQuery.sizeOf(context).width * 0.85),
        decoration: BoxDecoration(
          color: scheme.primaryContainer,
          borderRadius: BorderRadius.circular(12),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            SelectableText(text),
            for (final ref in attachmentRefs)
              Padding(
                padding: EdgeInsets.only(top: AppSpacing.xs),
                child: Chip(label: Text(ref.split('/').last, overflow: TextOverflow.ellipsis)),
              ),
          ],
        ),
      ),
    );
  }
}

({String label, String? detail}) toolActivityPresentation(String name, Map<String, dynamic> input) {
  final lower = name.toLowerCase();
  if (lower.startsWith('mcp') || lower.contains('mcp__')) {
    final tool = name.replaceFirst(RegExp(r'^mcp[_-]*', caseSensitive: false), '');
    return (label: 'mcp $tool'.trim(), detail: null);
  }
  final path = input['path'] ?? input['file_path'] ?? input['target_file'] ?? input['relative_path'];
  final pathStr = path?.toString();
  switch (lower) {
    case 'edit':
    case 'write':
    case 'strreplace':
      return (label: pathStr != null ? 'Изменён $pathStr' : 'Изменён файл', detail: null);
    case 'read':
    case 'read_file':
      return (label: pathStr != null ? 'Прочитан $pathStr' : 'Прочитан файл', detail: null);
    case 'shell':
    case 'bash':
    case 'run_terminal_cmd':
      final cmd = input['command'] ?? input['cmd'];
      return (label: 'Запущена команда', detail: cmd?.toString());
    default:
      return (label: name, detail: null);
  }
}

({int added, int removed})? parseDiffStats(Object? output) {
  if (output == null) return null;
  if (output is Map) {
    final add = output['lines_added'] ?? output['added_lines'] ?? output['additions'];
    final rem = output['lines_removed'] ?? output['removed_lines'] ?? output['deletions'];
    if (add is num || rem is num) {
      return (added: (add as num?)?.toInt() ?? 0, removed: (rem as num?)?.toInt() ?? 0);
    }
  }
  final text = output.toString();
  if (text.isEmpty) return null;
  var added = 0;
  var removed = 0;
  for (final line in text.split('\n')) {
    if (line.startsWith('+++') || line.startsWith('---') || line.startsWith('@@')) continue;
    if (line.startsWith('+')) added++;
    if (line.startsWith('-')) removed++;
  }
  if (added == 0 && removed == 0) return null;
  return (added: added, removed: removed);
}

String _formatPanelContent(Object? value) {
  if (value == null) return '';
  if (value is Map || value is List) return value.toString();
  return value.toString();
}

class ToolActivityBlock extends StatefulWidget {
  const ToolActivityBlock({
    super.key,
    required this.name,
    this.input = const {},
    this.output,
    this.isError = false,
    this.pending = false,
  });

  final String name;
  final Map<String, dynamic> input;
  final Object? output;
  final bool isError;
  final bool pending;

  @override
  State<ToolActivityBlock> createState() => _ToolActivityBlockState();
}

class _ToolActivityBlockState extends State<ToolActivityBlock> {
  bool _open = false;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final presentation = toolActivityPresentation(widget.name, widget.input);
    final stats = parseDiffStats(widget.output);
    final detail = presentation.detail ?? _formatPanelContent(widget.input);
    final outputText = _formatPanelContent(widget.output);

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

    final hasPanel = detail.isNotEmpty || outputText.isNotEmpty;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        ChatMutedLine(
          label: widget.pending ? '${presentation.label}…' : presentation.label,
          trailing: badge,
          expanded: _open,
          onTap: hasPanel ? () => setState(() => _open = !_open) : null,
        ),
        if (_open && hasPanel)
          ChatInsetPanel(
            child: SelectableText(
              outputText.isNotEmpty ? outputText : detail,
              style: _mutedBodyStyle(context).copyWith(
                fontFamily: widget.name.toLowerCase().contains('shell') ? 'monospace' : null,
              ),
            ),
          ),
      ],
    );
  }
}

class ToolCallBlock extends StatelessWidget {
  const ToolCallBlock({super.key, required this.name, this.input = const {}});

  final String name;
  final Map<String, dynamic> input;

  @override
  Widget build(BuildContext context) {
    return ToolActivityBlock(name: name, input: input, pending: true);
  }
}

class ToolResultBlock extends StatelessWidget {
  const ToolResultBlock({super.key, required this.name, this.output, this.isError = false});

  final String name;
  final Object? output;
  final bool isError;

  @override
  Widget build(BuildContext context) {
    return ToolActivityBlock(name: name, output: output, isError: isError);
  }
}

class ApprovalBlock extends StatelessWidget {
  const ApprovalBlock({
    super.key,
    required this.name,
    required this.approvalId,
    this.input = const {},
    this.onAllow,
    this.onDeny,
  });

  final String name;
  final String approvalId;
  final Map<String, dynamic> input;
  final VoidCallback? onAllow;
  final VoidCallback? onDeny;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return Padding(
      padding: EdgeInsets.symmetric(vertical: AppSpacing.xs),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(name, style: Theme.of(context).textTheme.titleSmall),
          SizedBox(height: AppSpacing.xs),
          ChatInsetPanel(child: SelectableText(input.toString())),
          SizedBox(height: AppSpacing.sm),
          Row(
            children: [
              FilledButton(onPressed: onAllow, child: Text(l10n.projectApproveAndContinue)),
              SizedBox(width: AppSpacing.sm),
              OutlinedButton(onPressed: onDeny, child: Text(l10n.projectDeny)),
            ],
          ),
        ],
      ),
    );
  }
}

class SubagentBlock extends StatefulWidget {
  const SubagentBlock({
    super.key,
    required this.title,
    this.events = const [],
    this.onFetchSidechain,
  });

  final String title;
  final List<dynamic> events;
  final Future<List<Map<String, dynamic>>> Function()? onFetchSidechain;

  @override
  State<SubagentBlock> createState() => _SubagentBlockState();
}

class _SubagentBlockState extends State<SubagentBlock> {
  List<Map<String, dynamic>>? _sidechain;
  bool _loading = false;
  bool _open = false;

  Future<void> _load() async {
    if (widget.onFetchSidechain == null || _loading) return;
    setState(() => _loading = true);
    try {
      _sidechain = await widget.onFetchSidechain!();
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        ChatMutedLine(
          label: widget.title,
          expanded: _open,
          onTap: () {
            setState(() => _open = !_open);
            if (_open) _load();
          },
        ),
        if (_open) ...[
          if (_loading) const LinearProgressIndicator(),
          ChatInsetPanel(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                for (final ev in _sidechain ?? widget.events)
                  Padding(
                    padding: EdgeInsets.only(bottom: AppSpacing.xs),
                    child: Text(ev.toString(), style: _mutedBodyStyle(context)),
                  ),
              ],
            ),
          ),
        ],
      ],
    );
  }
}

class PlanProgressBlock extends StatelessWidget {
  const PlanProgressBlock({super.key, required this.tasks});

  final List<dynamic> tasks;

  @override
  Widget build(BuildContext context) {
    final visible = tasks.whereType<Map>().where((t) {
      final title = t['title'] ?? t['id'];
      return title != null && '$title'.trim().isNotEmpty;
    }).toList();
    if (visible.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: EdgeInsets.symmetric(vertical: AppSpacing.xs),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          for (final task in visible)
            CheckboxListTile(
              contentPadding: EdgeInsets.zero,
              dense: true,
              value: task['status'] == 'done' || task['status'] == 'completed',
              onChanged: null,
              title: Text('${task['title'] ?? task['id']}'),
              controlAffinity: ListTileControlAffinity.leading,
            ),
        ],
      ),
    );
  }
}

class ThinkingBlock extends StatefulWidget {
  const ThinkingBlock({super.key, required this.text, this.durationMs, this.streaming = false});

  final String text;
  final int? durationMs;
  final bool streaming;

  @override
  State<ThinkingBlock> createState() => _ThinkingBlockState();
}

class _ThinkingBlockState extends State<ThinkingBlock> {
  bool _open = false;

  @override
  void initState() {
    super.initState();
    _open = widget.streaming;
  }

  @override
  void didUpdateWidget(covariant ThinkingBlock oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.streaming && !_open) {
      setState(() => _open = true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final title = widget.streaming
        ? l10n.projectChatReasoningStreaming
        : widget.durationMs != null
            ? '${l10n.projectChatReasoning} (${widget.durationMs}ms)'
            : l10n.projectChatReasoning;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        ChatMutedLine(
          label: title,
          expanded: _open,
          onTap: widget.text.isEmpty ? null : () => setState(() => _open = !_open),
        ),
        if (_open && widget.text.isNotEmpty)
          ChatInsetPanel(
            child: SelectableText(widget.text, style: _mutedBodyStyle(context)),
          ),
      ],
    );
  }
}

class UsageBlock extends StatefulWidget {
  const UsageBlock({super.key, required this.raw});

  final Map<String, dynamic> raw;

  @override
  State<UsageBlock> createState() => _UsageBlockState();
}

class _UsageBlockState extends State<UsageBlock> {
  bool _open = false;

  String? _formatNum(Object? value) {
    if (value == null) return null;
    return value.toString();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final rows = <Widget>[];
    void addRow(String label, Object? value) {
      final formatted = _formatNum(value);
      if (formatted == null) return;
      rows.add(Padding(
        padding: EdgeInsets.only(bottom: AppSpacing.xs),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            SizedBox(
              width: 140,
              child: Text(label, style: Theme.of(context).textTheme.bodySmall),
            ),
            Expanded(child: Text(formatted)),
          ],
        ),
      ));
    }

    addRow(l10n.projectChatUsageInputTokens, widget.raw['input_tokens']);
    addRow(l10n.projectChatUsageOutputTokens, widget.raw['output_tokens']);
    addRow(l10n.projectChatUsageTotalTokens, widget.raw['total_tokens']);
    addRow(l10n.projectChatUsageCost, widget.raw['total_cost_usd'] ?? widget.raw['cost_usd']);
    if (widget.raw['model'] != null) {
      addRow(l10n.projectChatModelLabel, widget.raw['model']);
    }

    if (rows.isEmpty) return const SizedBox.shrink();

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        ChatMutedLine(
          label: l10n.projectChatUsage,
          expanded: _open,
          onTap: () => setState(() => _open = !_open),
        ),
        if (_open)
          ChatInsetPanel(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: rows,
            ),
          ),
      ],
    );
  }
}
