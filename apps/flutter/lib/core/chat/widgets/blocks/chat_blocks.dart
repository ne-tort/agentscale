import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';

import 'package:prodavan/core/theme/app_spacing.dart';

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
          ChatMarkdownBody(text: text),
          if (streaming)
            Padding(
              padding: EdgeInsets.only(top: AppSpacing.xs),
              child: SizedBox(
                width: 12,
                height: 12,
                child: CircularProgressIndicator(strokeWidth: 2, color: scheme.primary),
              ),
            ),
          if (cancelled)
            Padding(
              padding: EdgeInsets.only(top: AppSpacing.xs),
              child: Text('Cancelled', style: Theme.of(context).textTheme.labelSmall),
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
  bool _open = false;

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: EdgeInsets.only(bottom: AppSpacing.sm),
      child: ExpansionTile(
        initiallyExpanded: _open,
        onExpansionChanged: (v) => setState(() => _open = v),
        title: Text('Running `${widget.name}`'),
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
  bool _open = false;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Card(
      margin: EdgeInsets.only(bottom: AppSpacing.sm),
      color: widget.isError ? scheme.errorContainer : null,
      child: ExpansionTile(
        initiallyExpanded: _open,
        onExpansionChanged: (v) => setState(() => _open = v),
        title: Text('Result: ${widget.name}'),
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
    return Card(
      margin: EdgeInsets.only(bottom: AppSpacing.sm),
      child: Padding(
        padding: EdgeInsets.all(AppSpacing.md),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Approve tool: $name', style: Theme.of(context).textTheme.titleSmall),
            SizedBox(height: AppSpacing.sm),
            SelectableText(input.toString()),
            SizedBox(height: AppSpacing.sm),
            Row(
              children: [
                FilledButton(onPressed: onAllow, child: const Text('Allow')),
                SizedBox(width: AppSpacing.sm),
                OutlinedButton(onPressed: onDeny, child: const Text('Deny')),
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
    this.status = 'running',
    this.events = const [],
    this.onFetchSidechain,
  });

  final String title;
  final String status;
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
        title: Row(
          children: [
            if (widget.status == 'running')
              Padding(
                padding: EdgeInsets.only(right: AppSpacing.sm),
                child: SizedBox(
                  width: 14,
                  height: 14,
                  child: CircularProgressIndicator(strokeWidth: 2),
                ),
              ),
            Expanded(child: Text(widget.title)),
          ],
        ),
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
  const PlanProgressBlock({super.key, required this.tasks, this.message});

  final List<dynamic> tasks;
  final String? message;

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: EdgeInsets.only(bottom: AppSpacing.sm),
      child: Padding(
        padding: EdgeInsets.all(AppSpacing.md),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (message != null) Text(message!, style: Theme.of(context).textTheme.titleSmall),
            for (final task in tasks)
              if (task is Map)
                CheckboxListTile(
                  value: task['status'] == 'done' || task['status'] == 'completed',
                  onChanged: null,
                  title: Text('${task['title'] ?? task['id'] ?? 'Task'}'),
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
  Widget build(BuildContext context) {
    return Card(
      margin: EdgeInsets.only(bottom: AppSpacing.sm),
      child: ExpansionTile(
        initiallyExpanded: _open,
        onExpansionChanged: (v) => setState(() => _open = v),
        title: Text(widget.streaming ? 'Thinking…' : 'Reasoning${widget.durationMs != null ? ' (${widget.durationMs}ms)' : ''}'),
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
