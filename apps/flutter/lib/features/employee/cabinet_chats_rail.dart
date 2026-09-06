import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_taper_hairline.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Peer nav block for chats — same geometry as [AppLayout] destinations.
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
    this.showLeadingDivider = false,
  });

  final bool extended;
  final bool newChatEnabled;
  final List<Map<String, dynamic>> pinned;
  final List<Map<String, dynamic>> projectChats;
  final String? activeSessionId;
  final VoidCallback? onNewChat;
  final void Function(Map<String, dynamic> chat) onOpenChat;

  /// When true and the chats block is non-empty, draw a taper hairline above
  /// (separates main rail destinations from chats).
  final bool showLeadingDivider;

  static const double _railMinWidth = 80;
  static const double _iconLabelGap = 8;

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
      for (final chat in pinned)
        _chatRow(context, chat, pinned: true, l10n: l10n),
      for (final chat in projectChats)
        _chatRow(context, chat, pinned: false, l10n: l10n),
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

  Widget _chatRow(
    BuildContext context,
    Map<String, dynamic> chat, {
    required bool pinned,
    required AppLocalizations l10n,
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
