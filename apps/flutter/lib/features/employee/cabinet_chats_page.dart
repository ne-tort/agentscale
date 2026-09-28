import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/features/employee/cabinet_chats_rail.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Full-page chats list for narrow cabinet shell (no modal sheets in
/// features). Thin wrapper: renders the chats rail tree extended.
class CabinetChatsPage extends StatelessWidget {
  const CabinetChatsPage({
    super.key,
    required this.newChatEnabled,
    required this.projectGroups,
    required this.collapsedProjectIds,
    required this.activeSessionId,
    required this.onNewChat,
    required this.onOpenChat,
    this.onToggleProjectCollapsed,
    this.onNewChatForProject,
  });

  final bool newChatEnabled;
  final List<Map<String, dynamic>> projectGroups;
  final Set<String> collapsedProjectIds;
  final String? activeSessionId;
  final VoidCallback? onNewChat;
  final void Function(Map<String, dynamic> chat) onOpenChat;
  final void Function(String projectId)? onToggleProjectCollapsed;
  final void Function(String projectId)? onNewChatForProject;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return AppScaffold(
      title: Text(l10n.navChats),
      body: ListView(
        padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
        children: [
          CabinetChatsRail(
            extended: true,
            newChatEnabled: newChatEnabled,
            projectGroups: projectGroups,
            collapsedProjectIds: collapsedProjectIds,
            activeSessionId: activeSessionId,
            onNewChat: onNewChat == null
                ? null
                : () {
                    Navigator.of(context).pop();
                    onNewChat!();
                  },
            onNewChatForProject: onNewChatForProject == null
                ? null
                : (projectId) {
                    Navigator.of(context).pop();
                    onNewChatForProject!(projectId);
                  },
            onToggleProjectCollapsed: onToggleProjectCollapsed,
            onOpenChat: (chat) {
              Navigator.of(context).pop();
              onOpenChat(chat);
            },
          ),
        ],
      ),
    );
  }
}
