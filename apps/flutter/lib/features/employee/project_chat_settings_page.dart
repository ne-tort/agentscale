import 'package:flutter/material.dart';

import 'package:prodavan/core/chat/controller/chat_session_controller.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
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

  String? _selectedValue() {
    final selected = widget.controller.selectedModel ?? widget.controller.defaultModel;
    if (selected == null || selected.isEmpty) return null;
    final ids = widget.controller.availableModels
        .map((m) => m['id'] as String? ?? m['label'] as String? ?? '')
        .where((id) => id.isNotEmpty)
        .toList();
    if (ids.contains(selected)) return selected;
    return ids.isEmpty ? null : ids.first;
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return AppScaffold(
      title: Text(l10n.projectChatSettingsTitle),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          InputDecorator(
            decoration: InputDecoration(
              labelText: l10n.projectChatModelLabel,
              border: const OutlineInputBorder(),
            ),
            child: DropdownButtonHideUnderline(
              child: DropdownButton<String>(
                isExpanded: true,
                value: _selectedValue(),
                items: widget.controller.availableModels
                    .map((m) {
                      final id = m['id'] as String? ?? m['label'] as String? ?? '';
                      return DropdownMenuItem<String>(
                        value: id,
                        child: Text(m['label'] as String? ?? id),
                      );
                    })
                    .where((item) => item.value != null && item.value!.isNotEmpty)
                    .toList(),
                onChanged: widget.controller.streaming
                    ? null
                    : (v) {
                        widget.controller.selectedModel = v;
                        widget.controller.notifyImmediate();
                        setState(() {});
                      },
              ),
            ),
          ),
        ],
      ),
    );
  }
}
