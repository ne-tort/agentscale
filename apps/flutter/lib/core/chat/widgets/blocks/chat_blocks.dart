import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:markdown/markdown.dart' as md;

import 'package:prodavan/core/chat/markdown_table_normalize.dart';
import 'package:prodavan/core/chat/models/chat_block.dart';
import 'package:prodavan/core/chat/thinking_duration.dart';
import 'package:prodavan/core/chat/tool_activity_labels.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/l10n/app_localizations.dart';

TextStyle _mutedTextStyle(BuildContext context) {
  final base = Theme.of(context).textTheme.bodyMedium ?? const TextStyle();
  return base.copyWith(
    color: Theme.of(context).colorScheme.onSurfaceVariant.withValues(alpha: 0.72),
  );
}

TextStyle _mutedBodyStyle(BuildContext context) {
  final base = Theme.of(context).textTheme.bodyMedium ?? const TextStyle();
  return base.copyWith(
    color: Theme.of(context).colorScheme.onSurfaceVariant.withValues(alpha: 0.72),
  );
}

class ChatMutedLine extends StatefulWidget {
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
  State<ChatMutedLine> createState() => _ChatMutedLineState();
}

class _ChatMutedLineState extends State<ChatMutedLine> {
  bool _hover = false;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final showChevron = widget.onTap != null && (widget.expanded || _hover);
    final chevron = widget.expanded ? Icons.expand_more : Icons.chevron_right;

    return MouseRegion(
      onEnter: (_) => setState(() => _hover = true),
      onExit: (_) => setState(() => _hover = false),
      child: GestureDetector(
        onTap: widget.onTap,
        behavior: HitTestBehavior.opaque,
        child: Padding(
          padding: EdgeInsets.symmetric(vertical: AppSpacing.xs / 2),
          child: Row(
            children: [
              Expanded(
                child: Text(widget.label, style: _mutedTextStyle(context)),
              ),
              if (widget.trailing != null) widget.trailing!,
              if (widget.onTap != null && showChevron)
                Padding(
                  padding: const EdgeInsets.only(left: 4),
                  child: Icon(chevron, size: 16, color: scheme.onSurfaceVariant.withValues(alpha: 0.72)),
                ),
            ],
          ),
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
    return Container(
      width: double.infinity,
      margin: EdgeInsets.only(top: AppSpacing.xs / 2),
      padding: EdgeInsets.symmetric(vertical: AppSpacing.xs),
      child: child,
    );
  }
}

/// Monospace panel matching assistant markdown codeblock look.
class ChatCodePanel extends StatelessWidget {
  const ChatCodePanel({super.key, required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    if (text.trim().isEmpty) return const SizedBox.shrink();
    return Container(
      width: double.infinity,
      margin: EdgeInsets.only(top: AppSpacing.xs / 2),
      padding: EdgeInsets.all(AppSpacing.sm),
      decoration: BoxDecoration(
        color: scheme.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(8),
      ),
      child: Text(
        text,
        style: Theme.of(context).textTheme.bodySmall?.copyWith(
              fontFamily: 'monospace',
              color: scheme.onSurface.withValues(alpha: 0.88),
            ),
      ),
    );
  }
}

class ChatMarkdownBody extends StatelessWidget {
  const ChatMarkdownBody({super.key, required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    if (text.isEmpty) return const SizedBox.shrink();
    final data = normalizeChatMarkdownTables(text);
    final base = MarkdownStyleSheet.fromTheme(Theme.of(context));
    return MarkdownBody(
      data: data,
      selectable: false,
      extensionSet: md.ExtensionSet.gitHubWeb,
      styleSheet: base.copyWith(
        p: Theme.of(context).textTheme.bodyMedium,
        code: TextStyle(
          fontFamily: 'monospace',
          backgroundColor: scheme.surfaceContainerHighest,
        ),
        codeblockDecoration: BoxDecoration(
          color: scheme.surfaceContainerHighest,
          borderRadius: BorderRadius.circular(8),
        ),
        tableHead: Theme.of(context).textTheme.bodyMedium?.copyWith(fontWeight: FontWeight.w600),
        tableBody: Theme.of(context).textTheme.bodyMedium,
        tableBorder: TableBorder.all(
          color: scheme.outlineVariant.withValues(alpha: 0.55),
          width: 1,
        ),
        tableHeadAlign: TextAlign.start,
        tableCellsPadding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
        tableColumnWidth: const IntrinsicColumnWidth(),
        tableScrollbarThumbVisibility: true,
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
          Text(text, style: Theme.of(context).textTheme.bodyMedium)
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
        margin: EdgeInsets.only(top: AppSpacing.md, bottom: AppSpacing.sm),
        padding: EdgeInsets.symmetric(horizontal: AppSpacing.md, vertical: AppSpacing.sm),
        constraints: BoxConstraints(maxWidth: MediaQuery.sizeOf(context).width * 0.85),
        decoration: BoxDecoration(
          color: scheme.primaryContainer,
          borderRadius: BorderRadius.circular(12),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            Text(text),
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
  // Legacy non-l10n fallback — prefer formatToolActivityLabel in widgets.
  final kind = normalizeToolKind(name);
  final path = extractToolContextPath(input);
  final pattern = extractToolContextPattern(input);
  final command = extractToolContextCommand(input);
  return switch (kind) {
    ToolKind.fileRead => (label: path != null ? 'Прочитан $path' : 'Прочитан файл', detail: null),
    ToolKind.fileWrite => (label: path != null ? 'Записан $path' : 'Записан файл', detail: null),
    ToolKind.fileEdit => (label: path != null ? 'Изменён $path' : 'Изменён файл', detail: null),
    ToolKind.fileDelete => (label: path != null ? 'Удалён $path' : 'Удалён файл', detail: null),
    ToolKind.searchGlob => (label: pattern != null ? 'Поиск файлов $pattern' : 'Поиск файлов', detail: null),
    ToolKind.searchGrep => (label: pattern != null ? 'Поиск $pattern' : 'Поиск', detail: null),
    ToolKind.listDir => (label: path != null ? 'Список $path' : 'Список файлов', detail: null),
    ToolKind.shell => (label: 'Запущена команда', detail: command),
    ToolKind.mcp => (label: 'mcp ${name.replaceFirst(RegExp(r'^mcp[_-]*', caseSensitive: false), '').trim()}'.trim(), detail: null),
    ToolKind.subagent => (label: 'Подагент $name', detail: null),
    ToolKind.generic => (label: 'Инструмент $name', detail: null),
  };
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
    final l10n = AppLocalizations.of(context);
    final scheme = Theme.of(context).colorScheme;
    final kind = normalizeToolKind(widget.name);
    final presentation = formatToolActivityLabel(
      l10n,
      name: widget.name,
      input: widget.input,
      output: widget.output,
      pending: widget.pending,
    );
    final stats = parseDiffStats(widget.output);
    final panelBody = formatToolPanelBody(
      kind: kind,
      input: widget.input,
      output: widget.output,
    );
    // Shell: show command as panel fallback when no stdout yet.
    final body = panelBody.isNotEmpty
        ? panelBody
        : (kind == ToolKind.shell && (presentation.detail?.isNotEmpty ?? false)
            ? presentation.detail!
            : '');

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

    final hasPanel = body.trim().isNotEmpty;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        ChatMutedLine(
          label: presentation.label,
          trailing: badge,
          expanded: _open,
          onTap: hasPanel ? () => setState(() => _open = !_open) : null,
        ),
        if (_open && hasPanel) ChatCodePanel(text: body),
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
          ChatInsetPanel(child: Text(input.toString())),
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
  bool _offline = false;

  Future<void> _load() async {
    if (widget.onFetchSidechain == null || _loading) return;
    setState(() {
      _loading = true;
      _offline = false;
    });
    try {
      _sidechain = await widget.onFetchSidechain!();
    } catch (_) {
      if (mounted) setState(() => _offline = true);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
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
          if (_offline)
            ChatInsetPanel(
              child: Text(l10n.projectChatSidechainOffline, style: _mutedBodyStyle(context)),
            )
          else
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
    final title = formatThinkingDurationLabel(
      l10n,
      durationMs: widget.durationMs,
      streaming: widget.streaming,
    );
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
            child: Text(widget.text, style: _mutedBodyStyle(context)),
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

/// Activity bucket for consecutive block grouping.
enum ActivityGroupKind {
  thinking,
  fileEdit,
  fileRead,
  fileDelete,
  searchGlob,
  searchGrep,
  listDir,
  command,
  mcp,
  generic,
}

class GroupedActivityBlock extends StatefulWidget {
  const GroupedActivityBlock({
    super.key,
    required this.kind,
    required this.items,
    required this.label,
    this.diffStats,
    required this.childBuilder,
  });

  final ActivityGroupKind kind;
  final List<({ChatBlock block, ChatBlock? paired})> items;
  final String label;
  final ({int added, int removed})? diffStats;
  final Widget Function(({ChatBlock block, ChatBlock? paired}) item) childBuilder;

  @override
  State<GroupedActivityBlock> createState() => _GroupedActivityBlockState();
}

class _GroupedActivityBlockState extends State<GroupedActivityBlock> {
  bool _open = false;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final stats = widget.diffStats;
    Widget? badge;
    if (stats != null && (stats.added > 0 || stats.removed > 0)) {
      badge = Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (stats.added > 0)
            Text('+${stats.added}', style: TextStyle(color: scheme.primary, fontSize: 12)),
          if (stats.added > 0 && stats.removed > 0) const SizedBox(width: 4),
          if (stats.removed > 0)
            Text('-${stats.removed}', style: TextStyle(color: scheme.error, fontSize: 12)),
        ],
      );
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        ChatMutedLine(
          label: widget.label,
          trailing: badge,
          expanded: _open,
          onTap: () => setState(() => _open = !_open),
        ),
        if (_open)
          ChatInsetPanel(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                for (final item in widget.items) widget.childBuilder(item),
              ],
            ),
          ),
      ],
    );
  }
}
