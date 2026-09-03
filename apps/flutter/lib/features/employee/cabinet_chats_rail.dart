import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Compact chats block for the cabinet NavigationRail (under logo / before destinations).
class CabinetChatsRail extends StatelessWidget {
  const CabinetChatsRail({
    super.key,
    required this.extended,
    required this.newChatEnabled,
    required this.pinned,
    required this.projectChats,
    required this.activeSessionId,
    required this.onNewChat,
    required this.onOpenChat,
  });

  final bool extended;
  final bool newChatEnabled;
  final List<Map<String, dynamic>> pinned;
  final List<Map<String, dynamic>> projectChats;
  final String? activeSessionId;
  final VoidCallback? onNewChat;
  final void Function(Map<String, dynamic> chat) onOpenChat;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final colors = context.appColors;
    final items = <Widget>[
      _sectionLabel(context, l10n.navChats, extended: extended),
      _row(
        context,
        icon: Icons.add_comment_outlined,
        label: l10n.navNewChat,
        selected: false,
        enabled: newChatEnabled && onNewChat != null,
        onTap: onNewChat,
        extended: extended,
      ),
      for (final chat in pinned)
        _chatRow(context, chat, pinned: true, colors: colors, l10n: l10n, extended: extended),
      for (final chat in projectChats)
        _chatRow(context, chat, pinned: false, colors: colors, l10n: l10n, extended: extended),
    ];

    return ConstrainedBox(
      constraints: const BoxConstraints(maxHeight: 280),
      child: ListView(
        shrinkWrap: true,
        padding: EdgeInsets.zero,
        children: items,
      ),
    );
  }

  Widget _sectionLabel(BuildContext context, String text, {required bool extended}) {
    if (!extended) return const SizedBox.shrink();
    final colors = context.appColors;
    return Padding(
      padding: const EdgeInsets.fromLTRB(AppSpacing.md, AppSpacing.sm, AppSpacing.md, AppSpacing.xs),
      child: Text(
        text.toUpperCase(),
        style: TextStyle(
          color: colors.muted,
          fontSize: 11,
          fontWeight: FontWeight.w600,
          letterSpacing: 0.6,
        ),
      ),
    );
  }

  Widget _chatRow(
    BuildContext context,
    Map<String, dynamic> chat, {
    required bool pinned,
    required AppColorTokens colors,
    required AppLocalizations l10n,
    required bool extended,
  }) {
    final sid = chat['session_id'] as String? ?? '';
    final title = (chat['title'] as String?)?.trim();
    final label = (title == null || title.isEmpty) ? l10n.chatUntitled : title;
    return _row(
      context,
      icon: pinned ? Icons.push_pin : Icons.chat_bubble_outline,
      label: label,
      selected: sid == activeSessionId,
      enabled: true,
      onTap: () => onOpenChat(chat),
      extended: extended,
    );
  }

  Widget _row(
    BuildContext context, {
    required IconData icon,
    required String label,
    required bool selected,
    required bool enabled,
    required VoidCallback? onTap,
    required bool extended,
  }) {
    final colors = context.appColors;
    final color = !enabled
        ? colors.muted.withValues(alpha: 0.45)
        : selected
            ? colors.primary
            : colors.onSurface;
    final child = extended
        ? Padding(
            padding: const EdgeInsets.symmetric(horizontal: AppSpacing.sm, vertical: 6),
            child: Row(
              children: [
                Icon(icon, size: 18, color: color),
                const SizedBox(width: AppSpacing.sm),
                Expanded(
                  child: Text(
                    label,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      color: color,
                      fontSize: 13,
                      fontWeight: selected ? FontWeight.w600 : FontWeight.w400,
                    ),
                  ),
                ),
              ],
            ),
          )
        : Padding(
            padding: const EdgeInsets.symmetric(vertical: 4),
            child: Icon(icon, size: 20, color: color),
          );

    return Material(
      color: selected ? colors.primary.withValues(alpha: 0.08) : Colors.transparent,
      child: InkWell(
        onTap: enabled ? onTap : null,
        child: child,
      ),
    );
  }
}
