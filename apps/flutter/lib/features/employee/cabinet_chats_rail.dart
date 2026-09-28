import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_taper_hairline.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Peer nav block for chats — same geometry as [AppLayout] destinations.
///
/// Tree layout: every project group is a collapsible branch header followed
/// by its chats (pinned chats pinned WITHIN their branch, backend-sorted).
/// In icon-only (collapsed rail) mode there are no per-project icons, so
/// chats of all projects render flat without headers.
class CabinetChatsRail extends StatelessWidget {
  const CabinetChatsRail({
    super.key,
    required this.extended,
    required this.newChatEnabled,
    required this.projectGroups,
    required this.activeSessionId,
    required this.onNewChat,
    required this.onOpenChat,
    this.collapsedProjectIds = const <String>{},
    this.onToggleProjectCollapsed,
    this.onNewChatForProject,
    this.showLeadingDivider = false,
  });

  final bool extended;
  final bool newChatEnabled;

  /// Sidebar tree groups:
  /// `{project_id, project_name, status, new_chat_enabled, chats: [...]}`.
  final List<Map<String, dynamic>> projectGroups;

  /// Collapsed project branch ids — everything else renders expanded.
  final Set<String> collapsedProjectIds;

  final String? activeSessionId;
  final VoidCallback? onNewChat;
  final void Function(Map<String, dynamic> chat) onOpenChat;

  /// Branch header tap — toggles collapse (owner persists the set).
  final void Function(String projectId)? onToggleProjectCollapsed;

  /// Start a new chat inside a specific project branch (rail renders the
  /// add button only when that group's new_chat_enabled is true).
  final void Function(String projectId)? onNewChatForProject;

  /// When true and the chats block is non-empty, draw a taper hairline above
  /// (separates main rail destinations from chats).
  final bool showLeadingDivider;

  static const double _railMinWidth = 80;
  static const double _iconLabelGap = 8;
  static const double _branchIndent = 8;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final items = <Widget>[
      if (newChatEnabled && onNewChat != null)
        _row(
          context,
          icon: Icons.add_comment_outlined,
          label: l10n.navNewChat,
          selected: false,
          enabled: true,
          onTap: onNewChat,
        ),
      if (!extended)
        // Icon-only rail: no per-project icons exist — render chats of all
        // projects flat (same look as before the tree).
        for (final group in projectGroups)
          for (final chat in _groupChats(group))
            _chatRow(context, chat, l10n: l10n),
      if (extended)
        for (final group in projectGroups) ...[
          _projectHeader(context, group),
          if (_isExpanded(group))
            for (final chat in _groupChats(group))
              Padding(
                padding: const EdgeInsets.only(left: _branchIndent),
                child: _chatRow(context, chat, l10n: l10n),
              ),
        ],
    ];

    if (items.isEmpty) {
      return const SizedBox.shrink();
    }

    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (showLeadingDivider) const AppTaperHairline(),
        ...items,
      ],
    );
  }

  List<Map<String, dynamic>> _groupChats(Map<String, dynamic> group) {
    final chats = group['chats'];
    if (chats is List) {
      return chats.whereType<Map<String, dynamic>>().toList();
    }
    return const <Map<String, dynamic>>[];
  }

  String _projectIdOf(Map<String, dynamic> group) =>
      group['project_id'] as String? ?? '';

  bool _isExpanded(Map<String, dynamic> group) =>
      !collapsedProjectIds.contains(_projectIdOf(group));

  /// Branch header: chevron + project name (+ chats count) + per-project
  /// new-chat button. Tap toggles collapse.
  Widget _projectHeader(BuildContext context, Map<String, dynamic> group) {
    final colors = context.appColors;
    final projectId = _projectIdOf(group);
    final expanded = _isExpanded(group);
    final rawName = (group['project_name'] as String?)?.trim();
    final label = (rawName == null || rawName.isEmpty) ? projectId : rawName;
    final count = _groupChats(group).length;
    final addChat = onNewChatForProject != null && group['new_chat_enabled'] == true;

    return InkWell(
      onTap: onToggleProjectCollapsed == null
          ? null
          : () => onToggleProjectCollapsed!(projectId),
      borderRadius: BorderRadius.circular(12),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
        child: Row(
          children: [
            SizedBox(
              width: _railMinWidth,
              child: Center(
                child: Icon(
                  expanded ? Icons.expand_more : Icons.chevron_right,
                  size: 18,
                  color: colors.muted,
                ),
              ),
            ),
            Expanded(
              child: Row(
                children: [
                  Flexible(
                    child: Text(
                      label,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        color: colors.onSurface,
                        fontSize: 14,
                        fontWeight: FontWeight.w500,
                      ),
                    ),
                  ),
                  if (count > 0)
                    Padding(
                      padding: const EdgeInsets.only(left: AppSpacing.xs),
                      child: Text(
                        '· $count',
                        maxLines: 1,
                        style: TextStyle(color: colors.muted, fontSize: 12),
                      ),
                    ),
                ],
              ),
            ),
            const SizedBox(width: _iconLabelGap),
            if (addChat) _branchAddButton(context, projectId),
          ],
        ),
      ),
    );
  }

  /// Small inline "+" that starts a chat in this project branch. Rendered
  /// whenever the branch allows new chats (hover brightens; touch devices
  /// have no hover, so it stays quietly visible).
  Widget _branchAddButton(BuildContext context, String projectId) {
    final colors = context.appColors;
    final onNewChatForProject = this.onNewChatForProject;
    if (onNewChatForProject == null) {
      return const SizedBox.shrink();
    }
    return IconButton(
      padding: EdgeInsets.zero,
      constraints: const BoxConstraints.tightFor(width: 32, height: 32),
      iconSize: 18,
      tooltip: AppLocalizations.of(context).navNewChat,
      color: colors.muted,
      icon: const Icon(Icons.add),
      onPressed: () => onNewChatForProject(projectId),
    );
  }

  Widget _chatRow(
    BuildContext context,
    Map<String, dynamic> chat, {
    required AppLocalizations l10n,
  }) {
    final pinned = chat['pinned'] == true;
    final sid = chat['session_id'] as String? ?? '';
    final title = (chat['title'] as String?)?.trim();
    final label = (title == null || title.isEmpty) ? l10n.chatUntitled : title;
    final hasDraft = chat['has_draft'] == true;
    final hasMessages = chat['has_messages'] == true || chat['last_message_at'] != null;
    final IconData icon;
    if (pinned) {
      icon = Icons.push_pin;
    } else if (hasDraft && !hasMessages) {
      icon = Icons.edit_note_outlined;
    } else {
      icon = Icons.chat_bubble_outline;
    }
    return _row(
      context,
      icon: icon,
      label: label,
      selected: sid == activeSessionId,
      enabled: true,
      onTap: () => onOpenChat(chat),
    );
  }

  Widget _railIconLabel({required Widget icon, required Widget label}) {
    if (!extended) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            icon,
            const SizedBox(height: AppSpacing.xs),
            label,
          ],
        ),
      );
    }
    return Row(
      children: [
        SizedBox(width: _railMinWidth, child: Center(child: icon)),
        Expanded(child: label),
        const SizedBox(width: _iconLabelGap),
      ],
    );
  }

  Widget _row(
    BuildContext context, {
    required IconData icon,
    required String label,
    required bool selected,
    required bool enabled,
    required VoidCallback? onTap,
  }) {
    final colors = context.appColors;
    final color = !enabled
        ? colors.muted.withValues(alpha: 0.45)
        : selected
            ? colors.primary
            : (extended ? colors.onSurface : colors.muted);
    final iconWidget = Icon(icon, size: 24, color: color);
    final labelWidget = Text(
      label,
      maxLines: 1,
      overflow: TextOverflow.ellipsis,
      style: TextStyle(
        color: color,
        fontSize: extended ? 14 : 12,
        fontWeight: selected ? FontWeight.w600 : FontWeight.normal,
      ),
    );

    return InkWell(
      onTap: enabled ? onTap : null,
      borderRadius: BorderRadius.circular(12),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
        child: _railIconLabel(icon: iconWidget, label: labelWidget),
      ),
    );
  }
}
