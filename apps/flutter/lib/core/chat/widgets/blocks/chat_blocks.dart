import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:markdown/markdown.dart' as md;

import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/l10n/app_localizations.dart';

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
    final scheme = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context);
    return Container(
      margin: EdgeInsets.only(bottom: AppSpacing.sm),
      padding: EdgeInsets.symmetric(horizontal: AppSpacing.md, vertical: AppSpacing.sm),
      decoration: BoxDecoration(
        color: scheme.surfaceContainerLow,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (streaming)
            SelectableText(text, style: Theme.of(context).textTheme.bodyMedium)
          else
            ChatMarkdownBody(text: text),
          if (cancelled)
            Padding(
              padding: EdgeInsets.only(top: AppSpacing.xs),
              child: Text(l10n.projectChatCancelled, style: Theme.of(context).textTheme.labelSmall),
            ),
        ],
      ),
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

class ToolCallBlock extends StatefulWidget {
  const ToolCallBlock({super.key, required this.name, this.input = const {}});

  final String name;
  final Map<String, dynamic> input;

  @override
  State<ToolCallBlock> createState() => _ToolCallBlockState();
}

class _ToolCallBlockState extends State<ToolCallBlock> {
  @override
  Widget build(BuildContext context) {
    return Card(
      margin: EdgeInsets.only(bottom: AppSpacing.sm),
      child: ExpansionTile(
        title: Text(widget.name),
        children: [
          Padding(
            padding: EdgeInsets.all(AppSpacing.md),
            child: SelectableText(widget.input.toString()),
          ),
        ],
      ),
    );
  }
}

class ToolResultBlock extends StatefulWidget {
  const ToolResultBlock({super.key, required this.name, this.output, this.isError = false});

  final String name;
  final Object? output;
  final bool isError;

  @override
  State<ToolResultBlock> createState() => _ToolResultBlockState();
}

class _ToolResultBlockState extends State<ToolResultBlock> {
  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Card(
      margin: EdgeInsets.only(bottom: AppSpacing.sm),
      color: widget.isError ? scheme.errorContainer : null,
      child: ExpansionTile(
        title: Text(widget.name),
        children: [
          Padding(
            padding: EdgeInsets.all(AppSpacing.md),
            child: SelectableText('${widget.output ?? ''}'),
          ),
        ],
      ),
    );
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
    return Card(
      margin: EdgeInsets.only(bottom: AppSpacing.sm),
      child: Padding(
        padding: EdgeInsets.all(AppSpacing.md),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(name, style: Theme.of(context).textTheme.titleSmall),
            SizedBox(height: AppSpacing.sm),
            SelectableText(input.toString()),
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
    return Card(
      margin: EdgeInsets.only(bottom: AppSpacing.sm),
      child: ExpansionTile(
        title: Text(widget.title),
        onExpansionChanged: (open) {
          if (open) _load();
        },
        children: [
          if (_loading) const LinearProgressIndicator(),
          for (final ev in _sidechain ?? widget.events)
            Padding(
              padding: EdgeInsets.symmetric(horizontal: AppSpacing.md, vertical: AppSpacing.xs),
              child: Text(ev.toString()),
            ),
        ],
      ),
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
    return Card(
      margin: EdgeInsets.only(bottom: AppSpacing.sm),
      child: Padding(
        padding: EdgeInsets.all(AppSpacing.md),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            for (final task in visible)
              CheckboxListTile(
                value: task['status'] == 'done' || task['status'] == 'completed',
                onChanged: null,
                title: Text('${task['title'] ?? task['id']}'),
                controlAffinity: ListTileControlAffinity.leading,
              ),
          ],
        ),
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
    return Card(
      margin: EdgeInsets.only(bottom: AppSpacing.sm),
      child: ExpansionTile(
        initiallyExpanded: _open,
        onExpansionChanged: (v) => setState(() => _open = v),
        title: Text(title),
        children: [
          Padding(
            padding: EdgeInsets.all(AppSpacing.md),
            child: SelectableText(widget.text),
          ),
        ],
      ),
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
    if (value is num) return value.toString();
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

    return Card(
      margin: EdgeInsets.only(bottom: AppSpacing.sm),
      child: ExpansionTile(
        initiallyExpanded: _open,
        onExpansionChanged: (v) => setState(() => _open = v),
        title: Text(l10n.projectChatUsage),
        children: [
          Padding(
            padding: EdgeInsets.all(AppSpacing.md),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: rows,
            ),
          ),
        ],
      ),
    );
  }
}
