import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_taper_hairline.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Chats block of the cabinet rail — a compact projects tree rendered under
/// the main nav destinations.
///
/// Tree layout: every project branch is a collapsible header (chevron + name,
/// no count badge) followed by text-only chat rows (pinned chats pinned WITHIN
/// their branch, backend-sorted). A branch with no chats yet renders a single
/// muted "New chat" draft row that starts a chat in that project.
///
/// Legacy fallback ([legacyLayout], older backend without `projects[]`):
/// keeps the global "New chat" tile above the tree.
///
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
    this.legacyLayout = false,
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

  /// When true and the chats block is non-empty, draw a section break above
  /// (separates main rail destinations from chats).
  final bool showLeadingDivider;

  /// Legacy fallback (backend without the `projects[]` tree): render the
  /// global "New chat" tile above the groups. Tree mode hides it — each
  /// branch has its own "+" plus an empty-branch draft row instead.
  final bool legacyLayout;

  /// Icon column width for legacy tiles (matches [AppLayout] destinations).
  static const double _railMinWidth = 80;

  /// Icon↔label gap for legacy tiles (Material destination padding).
  static const double _iconLabelGap = 8;

  /// Branch chevron size.
  static const double _chevronSize = 18;

  /// Chevron ↔ project name gap — tight, so the tree reads as one compact
  /// block instead of the wide nav-destination geometry.
  static const double _chevronLabelGap = 4;

  /// Chat rows sit this far right of the branch name.
  static const double _branchIndent = 8;

  /// Leading (left) padding of tree rows. NOT Material's
  /// `_horizontalDestinationPadding` (8px) — the effective left coordinate
  /// of nav-tile content is farther right: the destination icon (24px) is
  /// centered inside the 80px icon column ([_railMinWidth], cf.
  /// `_kRailMinWidth` / `_railIconLabel` in [AppLayout]), so its left edge
  /// sits at (80 - 24) / 2 = 28px from the rail edge. The branch header
  /// (chevron) aligns its left edge with that same vertical line, so the
  /// tree column visually lines up with the menu icons above it.
  static const double _treeHorizontalPadding = (_railMinWidth - 24) / 2;

  /// Absolute left offset of chat text: tree padding + under the branch
  /// name, +[_branchIndent]. Derived from [_treeHorizontalPadding], so the
  /// chats stay exactly under the branch name when the tree column shifts.
  static const double _chatIndent = _treeHorizontalPadding +
      _chevronSize +
      _chevronLabelGap +
      _branchIndent;

  /// Extra left padding for the chat label INSIDE the row's highlight
  /// container (added on top of the row's horizontal [AppSpacing.xs]):
  /// keeps the text clear of the rounded left corner of the highlight.
  static const double _chatTextInset = 6;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final items = <Widget>[
      if (legacyLayout && newChatEnabled && onNewChat != null)
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
            _ChatRow(
              chat: chat,
              extended: false,
              selected: (chat['session_id'] as String? ?? '') == activeSessionId,
              onOpenChat: onOpenChat,
            ),
      if (extended)
        for (final group in projectGroups) ...[
          _projectHeader(context, group),
          if (_isExpanded(group)) ..._branchRows(context, group, l10n: l10n),
        ],
    ];

    if (items.isEmpty) {
      return const SizedBox.shrink();
    }

    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (showLeadingDivider)
          extended ? _sectionHeader(context, l10n) : const AppTaperHairline(),
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

  /// Rows inside one expanded branch: chat rows — or, while the branch lists
  /// no chats at all, a single synthetic draft row ([_draftRow]).
  List<Widget> _branchRows(
    BuildContext context,
    Map<String, dynamic> group, {
    required AppLocalizations l10n,
  }) {
    final chats = _groupChats(group);
    if (chats.isEmpty) {
      return [_draftRow(context, group, l10n: l10n)];
    }
    return [
      for (final chat in chats)
        Padding(
          padding: const EdgeInsets.only(left: _chatIndent),
          child: _ChatRow(
            chat: chat,
            extended: true,
            selected: (chat['session_id'] as String? ?? '') == activeSessionId,
            onOpenChat: onOpenChat,
          ),
        ),
    ];
  }

  /// Synthetic "New chat" row inside an empty branch — same action as the
  /// per-branch "+", so a fresh project explains how to start. Disappears as
  /// soon as the branch lists a real chat; disabled (dim, no tap) when the
  /// branch does not allow new chats.
  Widget _draftRow(
    BuildContext context,
    Map<String, dynamic> group, {
    required AppLocalizations l10n,
  }) {
    final colors = context.appColors;
    final projectId = _projectIdOf(group);
    final start = onNewChatForProject;
    final enabled = group['new_chat_enabled'] == true && start != null;
    final base = Theme.of(context).textTheme.labelMedium ?? const TextStyle();
    return Padding(
      padding: const EdgeInsets.only(left: _chatIndent),
      child: InkWell(
        onTap: start != null && group['new_chat_enabled'] == true
            ? () => start(projectId)
            : null,
        borderRadius: BorderRadius.circular(12),
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: AppSpacing.xs),
          child: Text(
            l10n.navNewChat,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: base.copyWith(
              color: colors.muted.withValues(alpha: enabled ? 0.6 : 0.45),
            ),
          ),
        ),
      ),
    );
  }

  /// Branch header: chevron + project name + per-project new-chat button.
  /// Tap toggles collapse. No chat count badge — the compact tree carries
  /// the size implicitly. No hover background: the header is structural
  /// (a collapse toggle), chat rows keep their hover/selection highlight.
  Widget _projectHeader(BuildContext context, Map<String, dynamic> group) {
    final colors = context.appColors;
    final projectId = _projectIdOf(group);
    final expanded = _isExpanded(group);
    final rawName = (group['project_name'] as String?)?.trim();
    final label = (rawName == null || rawName.isEmpty) ? projectId : rawName;
    final addChat =
        onNewChatForProject != null && group['new_chat_enabled'] == true;
    final base = Theme.of(context).textTheme.labelMedium ?? const TextStyle();

    return InkWell(
      onTap: onToggleProjectCollapsed == null
          ? null
          : () => onToggleProjectCollapsed!(projectId),
      borderRadius: BorderRadius.circular(12),
      hoverColor: Colors.transparent,
      child: Padding(
        padding: const EdgeInsets.symmetric(
          horizontal: _treeHorizontalPadding,
          vertical: AppSpacing.sm,
        ),
        child: Row(
          children: [
            Icon(
              expanded ? Icons.expand_more : Icons.chevron_right,
              size: _chevronSize,
              color: colors.muted,
            ),
            const SizedBox(width: _chevronLabelGap),
            Expanded(
              child: Text(
                label,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: base.copyWith(
                  color: colors.onSurface,
                  fontSize: 13,
                  fontWeight: FontWeight.w600,
                ),
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
  /// whenever the branch allows new chats. Instead of the default
  /// IconButton hover circle the icon zooms up on hover (classic
  /// "zoom on hover"): the IconButton's ink is fully transparent and the
  /// scale animation lives INSIDE the button, so the 32×32 tap area keeps
  /// its geometry.
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
      hoverColor: Colors.transparent,
      focusColor: Colors.transparent,
      highlightColor: Colors.transparent,
      splashColor: Colors.transparent,
      icon: _ZoomOnHoverIcon(icon: Icon(Icons.add, size: 18, color: colors.muted)),
      onPressed: () => onNewChatForProject(projectId),
    );
  }

  /// Section break between the nav destinations and the chats tree: full
  /// vertical breathing room above, half below (the tree sits closer to its
  /// section break than to the nav tiles above it), plus a fading hairline
  /// with a centered label.
  Widget _sectionHeader(BuildContext context, AppLocalizations l10n) {
    final colors = context.appColors;
    return Padding(
      padding: EdgeInsets.only(
        top: AppSpacing.md,
        bottom: AppSpacing.md / 2,
      ),
      child: Row(
        children: [
          const Expanded(child: _TaperSide()),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: AppSpacing.sm),
            child: Text(
              l10n.navProjects,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(
                color: colors.muted.withValues(alpha: 0.6),
                fontSize: 11,
                letterSpacing: 0.5,
                fontWeight: FontWeight.w600,
              ),
            ),
          ),
          const Expanded(child: _TaperSide(reverse: true)),
        ],
      ),
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

  /// Legacy global "New chat" tile — same geometry as [AppLayout]
  /// destinations (icon column + label).
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

/// Icon that smoothly scales up on pointer hover and back down on exit.
///
/// Used for the per-branch "+" add-chat button: replaces the default
/// IconButton hover ink circle with a zoom effect ([AnimatedScale],
/// ~150ms, [Curves.easeOutCubic]). The animation wraps only the icon, so
/// the parent button's tap target is unaffected.
class _ZoomOnHoverIcon extends StatefulWidget {
  const _ZoomOnHoverIcon({required this.icon});

  final Widget icon;

  @override
  State<_ZoomOnHoverIcon> createState() => _ZoomOnHoverIconState();
}

class _ZoomOnHoverIconState extends State<_ZoomOnHoverIcon> {
  bool _hovered = false;

  @override
  Widget build(BuildContext context) {
    return MouseRegion(
      onEnter: (_) => setState(() => _hovered = true),
      onExit: (_) => setState(() => _hovered = false),
      child: AnimatedScale(
        scale: _hovered ? 1.3 : 1.0,
        duration: const Duration(milliseconds: 150),
        curve: Curves.easeOutCubic,
        child: widget.icon,
      ),
    );
  }
}

/// One chat row of the tree — text-only, no leading icons: the chevron
/// column carries the branch structure, titles stay compact (labelMedium).
///
/// Hover and selection share ONE background container: the highlight is
/// visible when the row is hovered OR selected — same color, same corner
/// radius, same geometry (the full row, paddings included) — so the
/// persistent selection is pixel-identical to the hover highlight.
/// Selection additionally emphasizes the text (primary / w600).
class _ChatRow extends StatefulWidget {
  const _ChatRow({
    required this.chat,
    required this.extended,
    required this.selected,
    required this.onOpenChat,
  });

  final Map<String, dynamic> chat;

  /// Extended rail: tree row with the shared hover/selection container.
  /// Icon-only (collapsed) rail: centered label, no background container
  /// (no room for one) — selection is text emphasis only.
  final bool extended;

  final bool selected;
  final void Function(Map<String, dynamic> chat) onOpenChat;

  @override
  State<_ChatRow> createState() => _ChatRowState();
}

class _ChatRowState extends State<_ChatRow> {
  bool _hovered = false;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final colors = context.appColors;
    final chat = widget.chat;
    final title = (chat['title'] as String?)?.trim();
    final label = (title == null || title.isEmpty) ? l10n.chatUntitled : title;
    final selected = widget.selected;
    final base = Theme.of(context).textTheme.labelMedium ?? const TextStyle();
    final text = Text(
      label,
      maxLines: 1,
      overflow: TextOverflow.ellipsis,
      style: base.copyWith(
        color: selected ? colors.primary : colors.onSurface,
        fontWeight: selected ? FontWeight.w600 : FontWeight.w500,
      ),
    );

    if (!widget.extended) {
      // Icon-only rail: centered label, same as before minus the icon.
      return InkWell(
        onTap: () => widget.onOpenChat(chat),
        borderRadius: BorderRadius.circular(12),
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: AppSpacing.xs),
          child: Center(child: text),
        ),
      );
    }

    // The row container paints the highlight for BOTH states — hover ink is
    // disabled (transparent) so hovering a selected row does not double the
    // tint: exactly one layer, identical color and geometry either way.
    // The label gets a little extra left padding INSIDE the highlight so the
    // text does not hug the rounded corner of the container.
    final highlight = colors.onSurface.withValues(alpha: 0.08);
    return InkWell(
      onTap: () => widget.onOpenChat(chat),
      borderRadius: BorderRadius.circular(12),
      onHover: (value) => setState(() => _hovered = value),
      hoverColor: Colors.transparent,
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: (_hovered || selected) ? highlight : null,
          borderRadius: BorderRadius.circular(12),
        ),
        child: Padding(
          padding: const EdgeInsets.only(
            left: AppSpacing.xs +
                CabinetChatsRail._chatTextInset,
            right: AppSpacing.xs,
            top: AppSpacing.xs,
            bottom: AppSpacing.xs,
          ),
          child: text,
        ),
      ),
    );
  }
}

/// One side of the chats-tree section header hairline: solid next to the
/// label, fading out toward the rail edge.
class _TaperSide extends StatelessWidget {
  const _TaperSide({this.reverse = false});

  /// Fade toward the right instead of the left.
  final bool reverse;

  @override
  Widget build(BuildContext context) {
    final colors = context.appColors;
    final near = colors.border.withValues(alpha: 0.85);
    final far = colors.border.withValues(alpha: 0);
    return SizedBox(
      height: 1,
      child: DecoratedBox(
        decoration: BoxDecoration(
          gradient: LinearGradient(
            colors: reverse ? [near, far] : [far, near],
          ),
        ),
      ),
    );
  }
}
