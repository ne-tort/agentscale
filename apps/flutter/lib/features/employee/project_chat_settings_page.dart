import 'package:flutter/material.dart';

import 'package:prodavan/core/chat/controller/chat_session_controller.dart';
import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_trailing_chevron.dart';
import 'package:prodavan/features/employee/project_chat_model_select_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Chat settings — model selection for the current project session.
class ProjectChatSettingsPage extends StatefulWidget {
  const ProjectChatSettingsPage({
    super.key,
    required this.controller,
  });

  final ChatSessionController controller;

  @override
  State<ProjectChatSettingsPage> createState() => _ProjectChatSettingsPageState();
}

class _ProjectChatSettingsPageState extends State<ProjectChatSettingsPage> {
  @override
  void initState() {
    super.initState();
    widget.controller.changes.listen((_) {
      if (mounted) setState(() {});
    });
  }

  Future<void> _pickModel() async {
    if (widget.controller.streaming) return;
    final picked = await ProjectChatModelSelectPage.push(
      context,
      models: widget.controller.availableModels,
      selectedModelId: widget.controller.selectedModel ?? widget.controller.defaultModel,
      enabled: !widget.controller.streaming,
    );
    if (picked == null || !mounted) return;
    widget.controller.selectedModel = picked;
    widget.controller.notifyImmediate();
    setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return AppScaffold(
      title: Text(l10n.projectChatSettingsTitle),
      body: ListView(
        padding: const EdgeInsets.all(AppSpacing.md),
        children: [
          AppPreferenceTile(
            title: l10n.projectChatModelLabel,
            subtitle: Text(widget.controller.selectedModelLabel),
            trailing: const AppTrailingChevron(),
            enabled: !widget.controller.streaming,
            onTap: _pickModel,
          ),
        ],
      ),
    );
  }
}
