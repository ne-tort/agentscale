import 'dart:async';
import 'dart:convert';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:intl/intl.dart';
import 'package:markdown/markdown.dart' as md;

import 'package:prodavan/core/chat/markdown_table_normalize.dart';
import 'package:prodavan/core/chat/models/chat_block.dart';
import 'package:prodavan/core/chat/models/chat_projection.dart';
import 'package:prodavan/core/chat/thinking_duration.dart';
import 'package:prodavan/core/chat/tool_activity_labels.dart';
import 'package:prodavan/core/chat/widgets/blocks/chat_block_renderer.dart';
import 'package:prodavan/core/chat/widgets/chat_display_grouping.dart';
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

String? _stringOrNull(Object? value) =>
    value is String && value.isNotEmpty ? value : null;

int? _intOrNull(Object? value) {
  if (value is int) return value;
  if (value is num) return value.toInt();
  return null;
}

double? _doubleOrNull(Object? value) {
  if (value is double) return value;
  if (value is num) return value.toDouble();
  return null;
}

/// Thousands grouping with a non-breaking space (no mid-number wraps).
String _formatTokens(int value) {
  final negative = value < 0;
  final digits = value.abs().toString();
  final buf = StringBuffer(negative ? '-' : '');
  for (var i = 0; i < digits.length; i++) {
    if (i > 0 && (digits.length - i) % 3 == 0) buf.write('\u00A0');
    buf.write(digits[i]);
  }
  return buf.toString();
}

/// `$X` with up to 4 decimals, trailing zeros trimmed but ≥2 decimals kept
/// (`$0.05`, `$1.27`, `$0.0128`).
String _formatCost(double value) {
  var s = value.toStringAsFixed(4);
  final dot = s.indexOf('.');
  if (dot >= 0) {
    while (s.endsWith('0') && s.length - dot - 1 > 2) {
      s = s.substring(0, s.length - 1);
    }
  }
  return '\$$s';
}

/// `HH:MM` clock label (locale-neutral 24h, tabular-friendly) — null without a ts.
String? _formatClockTime(DateTime? ts) {
  if (ts == null) return null;
  return DateFormat('HH:mm').format(ts.toLocal());
}

/// `m:ss` duration label (locale-neutral, no units): 45000 → "0:45", 83000 → "1:23".
String _formatDurationLabel(int ms) {
  final totalSeconds = ms <= 0 ? 0 : (ms / 1000).round();
  final minutes = totalSeconds ~/ 60;
  final seconds = totalSeconds % 60;
  return '$minutes:${seconds.toString().padLeft(2, '0')}';
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
        // Semi-transparent: inline-code background must not fully cover
        // the selection tint (RenderParagraph paints selection UNDER the
        // per-span background paints).
        code: TextStyle(
          fontFamily: 'monospace',
          backgroundColor: scheme.surfaceContainerHighest.withValues(alpha: 0.55),
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

/// Blinking cursor shown while assistant text streams.
class _StreamingCursor extends StatefulWidget {
  const _StreamingCursor();

  @override
  State<_StreamingCursor> createState() => _StreamingCursorState();
}

class _StreamingCursorState extends State<_StreamingCursor>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 600),
  )..repeat(reverse: true);

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final fontSize = Theme.of(context).textTheme.bodyMedium?.fontSize ?? 14;
    return FadeTransition(
      opacity: _controller,
      child: Container(
        width: 2,
        height: fontSize * 1.2,
        margin: const EdgeInsets.only(top: 2),
        color: scheme.primary,
      ),
    );
  }
}

class AssistantStreamBlock extends StatefulWidget {
  const AssistantStreamBlock({
    super.key,
    required this.text,
    this.streaming = false,
    this.cancelled = false,
    this.interrupted = false,
    this.usageRaw,
    this.costResolver,
    this.completedAt,
    this.turnMs,
  });

  final String text;
  final bool streaming;
  final bool cancelled;

  /// SSE connection dropped mid-turn: show an interrupted marker.
  final bool interrupted;

  /// Accumulated turn usage (tokens / model / cost) rendered as hover
  /// metadata below the message — replaces the standalone usage block.
  final Map<String, dynamic>? usageRaw;

  /// UI-side cost estimate from the models catalog; runtime `cost_usd` wins.
  final double? Function(String? model, int? inputTokens, int? outputTokens)? costResolver;

  /// Wall-clock moment the answer completed — always-visible `HH:MM` cell on
  /// the left of the metadata row.
  final DateTime? completedAt;

  /// How long the answer took (first output → completion), rendered as a
  /// hover-reveal `m:ss` cell at the end of the metadata group.
  final int? turnMs;

  @override
  State<AssistantStreamBlock> createState() => _AssistantStreamBlockState();
}

class _AssistantStreamBlockState extends State<AssistantStreamBlock> {
  bool _hover = false;
  bool _copied = false;
  Timer? _copiedReset;

  @override
  void dispose() {
    _copiedReset?.cancel();
    super.dispose();
  }

  Future<void> _copy() async {
    await Clipboard.setData(ClipboardData(text: widget.text));
    if (!mounted) return;
    _copiedReset?.cancel();
    setState(() => _copied = true);
    _copiedReset = Timer(const Duration(seconds: 2), () {
      if (mounted) setState(() => _copied = false);
    });
  }

  /// Metadata row under the message:
  /// [HH:MM always visible] · hover-reveal [model · вход: N · выход: N · $cost · m:ss] · copy (hover-reveal, flush right).
  ///
  /// The completion time is permanent — the row keeps its height instead of
  /// collapsing to zero. While mounted the row is FIXED at the copy button's
  /// 28px: the usage cells and the copy button fade in/out (AnimatedOpacity)
  /// inside that reserved space, so revealing the copy icon never re-layouts
  /// — the message above is not lifted on hover (or while the 2s copied
  /// confirmation is up, which must survive the pointer leaving). Without a
  /// completion time the row collapses to zero while hidden; the first hover
  /// mounts it at the same fixed height.
  Widget _metaRow(BuildContext context) {
    final usage = widget.usageRaw;
    final model = _stringOrNull(usage?['model']);
    final input = _intOrNull(usage?['input_tokens']);
    final output = _intOrNull(usage?['output_tokens']);
    var cost = _doubleOrNull(usage?['cost_usd']);
    cost ??= widget.costResolver?.call(model, input, output);
    final turnMs = _intOrNull(widget.turnMs);
    final hasAny = model != null || input != null || output != null || cost != null || turnMs != null;
    final metaVisible = !widget.streaming && hasAny && (_hover || _copied);
    final timeText = _formatClockTime(widget.completedAt);
    final rowVisible = timeText != null || metaVisible;
    // Fixed-height row (28px — the copy button's box): once the row is
    // mounted its geometry NEVER changes. The copy slot keeps its exact
    // 28×28 box whether or not the button is shown, and the usage cells fade
    // into the row without touching its height — so revealing the copy icon
    // on hover never re-layouts or lifts the message above. A small gap
    // (AppSpacing.xs) separates the metadata from the message body.
    return AnimatedSize(
      duration: const Duration(milliseconds: 160),
      curve: Curves.easeOut,
      child: rowVisible
          ? Padding(
              padding: const EdgeInsets.only(top: AppSpacing.xs),
              child: SizedBox(
                height: 28,
                child: Row(
                  children: [
                    if (timeText != null)
                      Center(child: Text(timeText, style: _metaStyle(context))),
                    AnimatedSwitcher(
                      duration: const Duration(milliseconds: 160),
                      child: metaVisible
                          ? Padding(
                              padding: EdgeInsets.only(
                                left: timeText != null ? 8 : 0,
                              ),
                              key: const ValueKey<bool>(true),
                              child: _buildUsageCells(
                                context,
                                model: model,
                                input: input,
                                output: output,
                                cost: cost,
                                turnMs: turnMs,
                              ),
                            )
                          : const SizedBox(height: 28, key: ValueKey<bool>(false)),
                    ),
                    const Spacer(),
                    AnimatedSwitcher(
                      duration: const Duration(milliseconds: 160),
                      // The placeholder keeps the exact copy-button box, so
                      // the swap never moves a single pixel around it.
                      child: metaVisible
                          ? _copyButton(context)
                          : const SizedBox(width: 28, height: 28),
                    ),
                  ],
                ),
              ),
            )
          : const SizedBox(width: double.infinity, height: 0),
    );
  }

  TextStyle _metaStyle(BuildContext context) {
    return _mutedTextStyle(context).copyWith(
      fontSize: 12,
      fontFeatures: const [FontFeature.tabularFigures()],
    );
  }

  Widget _buildUsageCells(
    BuildContext context, {
    String? model,
    int? input,
    int? output,
    double? cost,
    int? turnMs,
  }) {
    final l10n = AppLocalizations.of(context);
    final style = _metaStyle(context);
    final cells = <Widget>[
      if (model != null)
        ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 260),
          child: Text(
            model,
            style: style,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
        ),
      if (input != null)
        Text('${l10n.chatUsageInputLabel} ${_formatTokens(input)}', style: style),
      if (output != null)
        Text('${l10n.chatUsageOutputLabel} ${_formatTokens(output)}', style: style),
      if (cost != null) Text(_formatCost(cost), style: style),
      if (turnMs != null) Text(_formatDurationLabel(turnMs), style: style),
    ];
    final items = <Widget>[];
    for (var i = 0; i < cells.length; i++) {
      if (i > 0) items.add(const SizedBox(width: 8));
      items.add(cells[i]);
    }
    return Row(mainAxisSize: MainAxisSize.min, children: items);
  }

  Widget _copyButton(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final scheme = Theme.of(context).colorScheme;
    return SizedBox(
      width: 28,
      height: 28,
      child: IconButton(
        padding: EdgeInsets.zero,
        constraints: const BoxConstraints(minWidth: 28, minHeight: 28),
        iconSize: 16,
        tooltip: _copied ? l10n.chatCopiedMessage : l10n.chatCopyMessage,
        onPressed: _copy,
        icon: Icon(
          _copied ? Icons.check : Icons.copy_outlined,
          size: 16,
          color: _copied ? scheme.primary : null,
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    if (widget.text.isEmpty && !widget.cancelled && !widget.interrupted) {
      return const SizedBox.shrink();
    }
    return MouseRegion(
      onEnter: (_) => setState(() => _hover = true),
      onExit: (_) => setState(() => _hover = false),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (widget.streaming) ...[
            // Markdown while streaming too — no plain-text → markdown reflow
            // jump when the block completes.
            ChatMarkdownBody(text: widget.text),
            const _StreamingCursor(),
          ] else if (widget.text.isNotEmpty)
            ChatMarkdownBody(text: widget.text),
          if (widget.cancelled)
            Padding(
              padding: EdgeInsets.only(top: AppSpacing.xs),
              child: Text(l10n.projectChatCancelled, style: Theme.of(context).textTheme.labelSmall),
            ),
          if (widget.interrupted)
            Padding(
              padding: EdgeInsets.only(top: AppSpacing.xs),
              child: Text(l10n.projectChatInterrupted, style: Theme.of(context).textTheme.labelSmall),
            ),
          _metaRow(context),
        ],
      ),
    );
  }
}

/// "agentscale работает…" — turn is streaming but no block streams and no
/// tool call is pending: the agent is silent between events, and the user
/// must see it did not stop. With [reconnect] set it renders the
/// «Попытка реконнекта (n/y)…» line instead (provider-error retry wait).
class AgentWorkingIndicator extends StatefulWidget {
  const AgentWorkingIndicator({super.key, this.reconnect});

  /// Reconnect attempt info from the runtime status frame; null = working.
  final ReconnectIndicatorData? reconnect;

  @override
  State<AgentWorkingIndicator> createState() => _AgentWorkingIndicatorState();
}

/// Provider-error reconnect status (chat error policy) for [AgentWorkingIndicator].
class ReconnectIndicatorData {
  const ReconnectIndicatorData({this.attempt, this.maxAttempts, this.nextModel});

  final int? attempt;
  final int? maxAttempts;
  final String? nextModel;
}

class _AgentWorkingIndicatorState extends State<AgentWorkingIndicator>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1200),
  )..repeat();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  /// Staggered 3-dot pulse: cosine breathe at phases 0 / 0.33 / 0.66.
  double _dotOpacity(int index) {
    final phase = index / 3;
    final t = (_controller.value + phase) % 1.0;
    return 0.15 + 0.85 * (0.5 - 0.5 * math.cos(2 * math.pi * t));
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final scheme = Theme.of(context).colorScheme;
    // The l10n copy ends with "…" — the ellipsis is rendered as animated
    // dots instead, so strip any trailing dots from the base text.
    var label = l10n.projectChatAgentWorking;
    final reconnect = widget.reconnect;
    if (reconnect != null) {
      final attempt = reconnect.attempt;
      final max = reconnect.maxAttempts;
      if (attempt != null && max != null && max > 0) {
        label = l10n.projectChatReconnectingAttempt(attempt, max);
      } else {
        label = l10n.projectChatReconnecting;
      }
    }
    while (label.endsWith('…') || label.endsWith('.')) {
      label = label.substring(0, label.length - 1);
    }
    label = label.trimRight();
    return AnimatedBuilder(
      animation: _controller,
      builder: (context, _) => Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(
            label,
            style: _mutedTextStyle(context).copyWith(fontSize: 12.5),
          ),
          const SizedBox(width: 4),
          for (var i = 0; i < 3; i++)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 1),
              child: Container(
                width: 3,
                height: 3,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: scheme.onSurfaceVariant.withValues(alpha: _dotOpacity(i)),
                ),
              ),
            ),
        ],
      ),
    );
  }
}

class UserMessageBlock extends StatefulWidget {
  const UserMessageBlock({
    super.key,
    required this.text,
    this.attachmentRefs = const [],
    this.attachments = const [],
    this.timestamp,
  });

  final String text;
  final List<String> attachmentRefs;
  final List<Map<String, dynamic>> attachments;

  /// Send time — muted `HH:MM` label right-aligned under the bubble.
  final DateTime? timestamp;

  @override
  State<UserMessageBlock> createState() => _UserMessageBlockState();
}

class _UserMessageBlockState extends State<UserMessageBlock> {
  final Set<int> _openSpoilers = {};

  String _attachmentLabel(AppLocalizations l10n, Map<String, dynamic> att) {
    final name = att['filename'] as String? ?? 'file';
    final kind = att['kind'] as String? ?? '';
    final rows = att['row_count'];
    if (kind == 'inline_json' || kind == 'inline_table') {
      return rows is int
          ? l10n.chatAttachmentRowsLabel(name, rows)
          : l10n.chatAttachmentJsonLabel(name);
    }
    final path = att['workspace_path'] as String?;
    if (path != null && path.isNotEmpty) {
      return l10n.chatAttachmentPathLabel(name, path);
    }
    return l10n.chatAttachmentLabel(name);
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final scheme = Theme.of(context).colorScheme;
    final attachments = widget.attachments;
    final refs = widget.attachmentRefs;
    final timeText = _formatClockTime(widget.timestamp);
    return Align(
      alignment: Alignment.centerRight,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          Container(
            margin: EdgeInsets.only(top: AppSpacing.md, bottom: timeText != null ? 0 : AppSpacing.sm),
            padding: EdgeInsets.symmetric(horizontal: AppSpacing.md, vertical: AppSpacing.sm),
            constraints: BoxConstraints(maxWidth: MediaQuery.sizeOf(context).width * 0.85),
            decoration: BoxDecoration(
              color: scheme.primaryContainer,
              borderRadius: BorderRadius.circular(12),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                if (widget.text.isNotEmpty) Text(widget.text),
                if (attachments.isNotEmpty)
                  for (var i = 0; i < attachments.length; i++) ...[
                    Builder(builder: (context) {
                      final att = attachments[i];
                      final kind = att['kind'] as String? ?? '';
                      final inlineJson =
                          kind == 'inline_json' ? att['inline_json'] : null;
                      // Tabular attachments carry a GFM markdown table —
                      // rendered as a real table instead of a JSON dump.
                      final inlineMarkdown =
                          kind == 'inline_table' ? att['inline_markdown'] as String? : null;
                      final expandable = inlineJson != null || inlineMarkdown != null;
                      return Padding(
                        padding: const EdgeInsets.only(top: AppSpacing.xs),
                        child: _AttachmentSpoiler(
                          label: _attachmentLabel(l10n, att),
                          expanded: _openSpoilers.contains(i),
                          onTap: expandable
                              ? () => setState(() {
                                    if (_openSpoilers.contains(i)) {
                                      _openSpoilers.remove(i);
                                    } else {
                                      _openSpoilers.add(i);
                                    }
                                  })
                              : null,
                          body: inlineJson != null && _openSpoilers.contains(i)
                              ? JsonEncoder.withIndent('  ').convert(inlineJson)
                              : null,
                          bodyWidget:
                              inlineMarkdown != null && _openSpoilers.contains(i)
                                  ? ChatMarkdownBody(text: inlineMarkdown)
                                  : null,
                        ),
                      );
                    }),
                  ]
                else
                  for (final ref in refs)
                    Padding(
                      padding: EdgeInsets.only(top: AppSpacing.xs),
                      child: Chip(label: Text(ref.split('/').last, overflow: TextOverflow.ellipsis)),
                    ),
              ],
            ),
          ),
          if (timeText != null)
            Padding(
              padding: EdgeInsets.only(top: AppSpacing.xs / 2, bottom: AppSpacing.sm),
              child: Text(
                timeText,
                style: _mutedTextStyle(context).copyWith(
                  fontSize: 11,
                  fontFeatures: const [FontFeature.tabularFigures()],
                ),
              ),
            ),
        ],
      ),
    );
  }
}

class _AttachmentSpoiler extends StatelessWidget {
  const _AttachmentSpoiler({
    required this.label,
    required this.expanded,
    this.onTap,
    this.body,
    this.bodyWidget,
  });

  final String label;
  final bool expanded;
  final VoidCallback? onTap;
  final String? body;

  /// Rich body (markdown table) — takes precedence over [body] when set.
  final Widget? bodyWidget;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.end,
      children: [
        ChatMutedLine(
          label: label,
          expanded: expanded,
          onTap: onTap,
        ),
        if (expanded && bodyWidget != null)
          Padding(
            padding: const EdgeInsets.only(top: AppSpacing.xs),
            child: Align(
              alignment: Alignment.centerLeft,
              child: bodyWidget,
            ),
          )
        else if (expanded && body != null && body!.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: AppSpacing.xs),
            child: ChatCodePanel(text: body!),
          ),
      ],
    );
  }
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

/// Subagent (sidechain) task line: muted collapsible header + result summary,
/// live transcript when expanded.
///
/// - Collapsed: one-line muted summary of what the child agent concluded
///   (Claude-style task line); spinner while it is still running.
/// - Expanded: fetches the sidechain transcript (REST) and renders it as a
///   mini chat (markdown / thinking / tool activity) via [ChatBlockRenderer]
///   WITHOUT api/session wiring, so nested subagents never recurse into
///   fetching their own sidechains. While the parent turn streams, the
///   transcript is polled every 2.5 s; a failed fetch falls back to the
///   live `events` captured by the projection.
class SubagentBlock extends StatefulWidget {
  const SubagentBlock({
    super.key,
    required this.title,
    this.events = const [],
    this.resultSummary,
    this.running = false,
    this.onFetchSidechain,
  });

  final String title;

  /// Child events captured live by the projection (`subagent_event`).
  final List<dynamic> events;

  /// One-line conclusion emitted by `subagent_stop`.
  final String? resultSummary;

  /// Parent turn is streaming and no result yet: header spinner + polling.
  final bool running;

  /// Fetches the sidechain transcript blocks (server-rendered chat blocks).
  final Future<List<Map<String, dynamic>>> Function()? onFetchSidechain;

  @override
  State<SubagentBlock> createState() => _SubagentBlockState();
}

class _SubagentBlockState extends State<SubagentBlock> {
  static const _pollInterval = Duration(milliseconds: 2500);

  List<Map<String, dynamic>>? _sidechain;
  bool _loading = false;
  bool _open = false;
  bool _offline = false;
  Timer? _pollTimer;

  @override
  void didUpdateWidget(covariant SubagentBlock oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!widget.running && oldWidget.running) {
      _pollTimer?.cancel();
      _pollTimer = null;
    } else if (widget.running && _pollTimer == null) {
      // Turn resumed streaming (or fetching capability appeared) — resume
      // live transcript polling if the block is expanded.
      _schedulePoll();
    }
  }

  @override
  void dispose() {
    _pollTimer?.cancel();
    _pollTimer = null;
    super.dispose();
  }

  Future<void> _load() async {
    final fetch = widget.onFetchSidechain;
    if (fetch == null || _loading) return;
    // Finished subagent with a cached transcript — no refetch on re-expand.
    if (_sidechain != null && !widget.running) return;
    setState(() {
      _loading = true;
      _offline = false;
    });
    try {
      final blocks = await fetch();
      if (!mounted) return;
      setState(() => _sidechain = blocks);
    } catch (_) {
      if (mounted) setState(() => _offline = true);
    } finally {
      if (mounted) setState(() => _loading = false);
      _schedulePoll();
    }
  }

  /// While running and expanded: refresh the transcript on a timer.
  void _schedulePoll() {
    _pollTimer?.cancel();
    _pollTimer = null;
    if (!mounted || !widget.running || !_open || widget.onFetchSidechain == null) return;
    _pollTimer = Timer(_pollInterval, _load);
  }

  Widget? _headerTrailing(BuildContext context) {
    final muted = Theme.of(context).colorScheme.onSurfaceVariant.withValues(alpha: 0.72);
    if (widget.running) {
      return SizedBox(
        width: 12,
        height: 12,
        child: CircularProgressIndicator(strokeWidth: 1.8, color: muted),
      );
    }
    if (widget.resultSummary != null) {
      return Padding(
        padding: const EdgeInsets.only(right: 2),
        child: Icon(Icons.check_circle_outline, size: 14, color: muted),
      );
    }
    return null;
  }

  /// Compact muted line per live child event: `type · excerpt` (≤120 chars).
  List<Widget> _eventLines(BuildContext context) {
    final style = _mutedBodyStyle(context).copyWith(fontSize: 12);
    return [
      for (final ev in widget.events)
        if (ev is Map)
          Padding(
            padding: EdgeInsets.only(bottom: AppSpacing.xs / 2),
            child: Text(
              _subagentEventLine(ev),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: style,
            ),
          ),
    ];
  }

  static String _subagentEventLine(Map ev) {
    var type = '';
    final t = ev['type'] ?? ev['kind'];
    if (t is String && t.trim().isNotEmpty) type = t.trim();
    String? excerpt;
    final data = ev['data'];
    for (final candidate in [
      ev['text'],
      if (data is Map) data['text'],
      if (data is Map) data['content'],
      ev['message'],
      ev['output'],
    ]) {
      if (candidate is String && candidate.trim().isNotEmpty) {
        excerpt = candidate;
        break;
      }
    }
    if (excerpt == null) return type.isEmpty ? 'event' : type;
    final flat = excerpt.replaceAll('\n', ' ').trim();
    final cut = flat.length <= 120 ? flat : '${flat.substring(0, 120)}…';
    return type.isEmpty ? cut : '$type · $cut';
  }

  /// Fetched sidechain rendered as a mini transcript (no api/session wiring:
  /// nested subagent blocks stay inert — no recursive sidechain fetching).
  Widget _transcriptBody(BuildContext context) {
    final blocks = chatBlocksFromTranscript(_sidechain);
    if (blocks.isEmpty) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: _eventLines(context),
      );
    }
    final pairs = mergeToolPairs(blocks);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (final pair in pairs)
          ChatBlockRenderer(
            key: ValueKey('sc-${pair.block.key}'),
            block: pair.block,
            pairedToolResult: pair.paired,
          ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final summary = widget.resultSummary;
    final eventLines = _open ? _eventLines(context) : const <Widget>[];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        ChatMutedLine(
          label: widget.title,
          trailing: _headerTrailing(context),
          expanded: _open,
          onTap: () {
            setState(() => _open = !_open);
            if (_open) {
              _load();
            } else {
              _pollTimer?.cancel();
              _pollTimer = null;
            }
          },
        ),
        if (!_open && summary != null && summary.trim().isNotEmpty)
          Padding(
            padding: EdgeInsets.only(bottom: AppSpacing.xs / 2),
            child: Text(
              summary,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: _mutedBodyStyle(context).copyWith(fontSize: 12),
            ),
          ),
        if (_open) ...[
          if (_loading) const LinearProgressIndicator(),
          if (_offline) ...[
            ChatInsetPanel(
              child: Text(l10n.projectChatSidechainOffline, style: _mutedBodyStyle(context)),
            ),
            if (eventLines.isNotEmpty)
              ChatInsetPanel(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: eventLines,
                ),
              ),
          ] else if (_sidechain != null)
            ChatInsetPanel(child: _transcriptBody(context))
          else if (eventLines.isNotEmpty)
            ChatInsetPanel(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: eventLines,
              ),
            ),
        ],
      ],
    );
  }
}

class PlanProgressBlock extends StatelessWidget {
  const PlanProgressBlock({super.key, required this.tasks, this.message});

  final List<dynamic> tasks;
  final String? message;

  @override
  Widget build(BuildContext context) {
    final visible = tasks.whereType<Map>().where((t) {
      final title = t['title'] ?? t['id'];
      return title != null && '$title'.trim().isNotEmpty;
    }).toList();
    if (visible.isEmpty) return const SizedBox.shrink();
    final intro = _stringOrNull(message);
    return Padding(
      padding: EdgeInsets.symmetric(vertical: AppSpacing.xs),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (intro != null) ChatMutedLine(label: intro),
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
  bool _userToggled = false;

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
    } else if (oldWidget.streaming && !widget.streaming && !_userToggled && _open) {
      // Stream finished and the user never touched the spoiler — collapse.
      setState(() => _open = false);
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
          onTap: widget.text.isEmpty
              ? null
              : () => setState(() {
                    _userToggled = true;
                    _open = !_open;
                  }),
        ),
        if (_open && widget.text.isNotEmpty)
          ChatInsetPanel(
            child: Text(widget.text, style: _mutedBodyStyle(context)),
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
