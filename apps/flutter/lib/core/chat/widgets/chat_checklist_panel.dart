/// Right-side checklist panel for the chat: renders the agent's live task
/// list (from `todo.write` / `plan` blocks — see [ChatChecklistTask]).
///
/// Two modes, both bounded (never full-screen):
/// * compact — a pill "x/y задач выполнено" that sits at the top when the
///   panel is collapsed;
/// * expanded — a fixed-width card with a capped height and its own inner
///   scroll, so a long task list never pushes the layout around.
library;

import 'package:flutter/material.dart';

import 'package:prodavan/core/chat/models/chat_checklist.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Expanded card width (px). Comfortable for task titles without dominating.
const double kChatChecklistWidth = 296;

/// Expanded card height cap (px) — inner list scrolls beyond this.
const double kChatChecklistMaxHeight = 440;

class ChatChecklistPanel extends StatefulWidget {
  const ChatChecklistPanel({
    super.key,
    required this.tasks,
    this.initialExpanded = true,
  });

  final List<ChatChecklistTask> tasks;
  final bool initialExpanded;

  @override
  State<ChatChecklistPanel> createState() => _ChatChecklistPanelState();
}

class _ChatChecklistPanelState extends State<ChatChecklistPanel> {
  late bool _expanded = widget.initialExpanded;

  /// Ids of tasks whose comment is expanded (hidden by default).
  final Set<String> _openComments = {};

  @override
  Widget build(BuildContext context) {
    if (widget.tasks.isEmpty) return const SizedBox.shrink();
    final l10n = AppLocalizations.of(context);
    final scheme = Theme.of(context).colorScheme;
    final done = checklistCompletedCount(widget.tasks);
    final total = widget.tasks.length;

    return AnimatedSize(
      duration: const Duration(milliseconds: 180),
      curve: Curves.easeOut,
      alignment: Alignment.topCenter,
      child: _expanded
          ? _expandedCard(context, l10n, scheme, done, total)
          : _compactPill(context, l10n, scheme, done, total),
    );
  }

  Widget _compactPill(
    BuildContext context,
    AppLocalizations l10n,
    ColorScheme scheme,
    int done,
    int total,
  ) {
    final allDone = done == total;
    return Material(
      color: scheme.surfaceContainerHighest,
      borderRadius: BorderRadius.circular(999),
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: () => setState(() => _expanded = true),
        child: Padding(
          padding: const EdgeInsets.symmetric(
            horizontal: AppSpacing.md,
            vertical: AppSpacing.sm,
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              _ProgressRing(done: done, total: total, size: 18, stroke: 2),
              const SizedBox(width: AppSpacing.sm),
              Text(
                l10n.chatChecklistProgress(done, total),
                style: TextStyle(
                  fontSize: 12.5,
                  fontWeight: FontWeight.w600,
                  color: allDone ? scheme.primary : scheme.onSurface,
                ),
              ),
              const SizedBox(width: AppSpacing.xs),
              Icon(Icons.unfold_more, size: 16, color: scheme.onSurfaceVariant),
            ],
          ),
        ),
      ),
    );
  }

  Widget _expandedCard(
    BuildContext context,
    AppLocalizations l10n,
    ColorScheme scheme,
    int done,
    int total,
  ) {
    return Container(
      width: kChatChecklistWidth,
      constraints: const BoxConstraints(maxHeight: kChatChecklistMaxHeight),
      decoration: BoxDecoration(
        color: scheme.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: scheme.outlineVariant.withValues(alpha: 0.5)),
      ),
      clipBehavior: Clip.antiAlias,
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _header(context, l10n, scheme, done, total),
          const Divider(height: 1),
          Flexible(
            child: ListView.builder(
              shrinkWrap: true,
              padding: const EdgeInsets.symmetric(vertical: AppSpacing.xs),
              itemCount: widget.tasks.length,
              itemBuilder: (context, i) => _taskRow(context, scheme, widget.tasks[i]),
            ),
          ),
        ],
      ),
    );
  }

  Widget _header(
    BuildContext context,
    AppLocalizations l10n,
    ColorScheme scheme,
    int done,
    int total,
  ) {
    return InkWell(
      onTap: () => setState(() => _expanded = false),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(
          AppSpacing.md,
          AppSpacing.sm,
          AppSpacing.xs,
          AppSpacing.sm,
        ),
        child: Row(
          children: [
            Icon(Icons.checklist_rounded, size: 18, color: scheme.primary),
            const SizedBox(width: AppSpacing.sm),
            Text(
              l10n.chatChecklistTitle,
              style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w700),
            ),
            const Spacer(),
            Text(
              '$done/$total',
              style: TextStyle(
                fontSize: 12.5,
                fontWeight: FontWeight.w600,
                color: scheme.onSurfaceVariant,
                fontFeatures: const [FontFeature.tabularFigures()],
              ),
            ),
            Icon(Icons.unfold_less, size: 16, color: scheme.onSurfaceVariant),
          ],
        ),
      ),
    );
  }

  Widget _taskRow(BuildContext context, ColorScheme scheme, ChatChecklistTask task) {
    final (IconData icon, Color color) = _taskIcon(scheme, task);
    final comment = task.comment;
    final hasComment = comment != null && comment.trim().isNotEmpty;
    final open = hasComment && _openComments.contains(task.id);
    return Padding(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.md,
        vertical: AppSpacing.xs,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          InkWell(
            onTap: hasComment
                ? () => setState(() {
                      if (open) {
                        _openComments.remove(task.id);
                      } else {
                        _openComments.add(task.id);
                      }
                    })
                : null,
            borderRadius: BorderRadius.circular(6),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Padding(
                  padding: const EdgeInsets.only(top: 1),
                  child: Icon(icon, size: 16, color: color),
                ),
                const SizedBox(width: AppSpacing.sm),
                Expanded(
                  child: Text(
                    task.title,
                    style: TextStyle(
                      fontSize: 12.5,
                      height: 1.3,
                      color: task.isCompleted ? scheme.onSurfaceVariant : scheme.onSurface,
                      decoration: task.isCompleted ? TextDecoration.lineThrough : null,
                      decorationColor: scheme.onSurfaceVariant,
                    ),
                  ),
                ),
                if (hasComment)
                  Padding(
                    padding: const EdgeInsets.only(left: AppSpacing.xs),
                    child: Icon(
                      open ? Icons.expand_less : Icons.expand_more,
                      size: 14,
                      color: scheme.onSurfaceVariant,
                    ),
                  ),
              ],
            ),
          ),
          if (open)
            Padding(
              padding: const EdgeInsets.only(left: 24, top: AppSpacing.xs, right: AppSpacing.xs),
              child: Text(
                comment,
                style: TextStyle(
                  fontSize: 11.5,
                  height: 1.3,
                  color: scheme.onSurfaceVariant,
                ),
              ),
            ),
        ],
      ),
    );
  }

  /// Leading icon + color for a task: an explicit color wins, otherwise the
  /// state drives the icon; completed/blocked/deferred/partial have distinct
  /// glyphs so the list is not merely open/closed.
  (IconData, Color) _taskIcon(ColorScheme scheme, ChatChecklistTask task) {
    if (task.isCompleted) return (Icons.check_circle_rounded, scheme.primary);
    switch (task.color) {
      case ChatTaskColor.success:
        return (Icons.check_circle_outline_rounded, scheme.primary);
      case ChatTaskColor.warning:
        return (Icons.warning_amber_rounded, scheme.tertiary);
      case ChatTaskColor.error:
        return (Icons.error_outline_rounded, scheme.error);
      case ChatTaskColor.info:
        return (Icons.info_outline_rounded, scheme.secondary);
      case null:
        break;
    }
    if (task.isInProgress) return (Icons.pending_rounded, scheme.tertiary);
    if (task.isBlocked) return (Icons.block_rounded, scheme.error);
    if (task.isDeferred) return (Icons.schedule_rounded, scheme.onSurfaceVariant);
    if (task.isPartial) return (Icons.incomplete_circle_rounded, scheme.tertiary);
    return (Icons.radio_button_unchecked, scheme.outline);
  }
}

/// Small circular progress ring "done/total" used in the compact pill.
class _ProgressRing extends StatelessWidget {
  const _ProgressRing({
    required this.done,
    required this.total,
    required this.size,
    required this.stroke,
  });

  final int done;
  final int total;
  final double size;
  final double stroke;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final value = total == 0 ? 0.0 : done / total;
    return SizedBox(
      width: size,
      height: size,
      child: Stack(
        alignment: Alignment.center,
        children: [
          CircularProgressIndicator(
            value: value,
            strokeWidth: stroke,
            backgroundColor: scheme.outlineVariant.withValues(alpha: 0.5),
            valueColor: AlwaysStoppedAnimation<Color>(scheme.primary),
          ),
          if (done == total)
            Icon(Icons.check, size: size * 0.6, color: scheme.primary),
        ],
      ),
    );
  }
}
