import 'dart:convert';

import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/l10n/app_localizations.dart';

class ToolApprovePage extends StatelessWidget {
  const ToolApprovePage({
    super.key,
    required this.projectId,
    required this.sessionId,
    required this.approval,
    required this.onDecision,
  });

  final String projectId;
  final String sessionId;
  final Map<String, dynamic> approval;
  final Future<void> Function(String decision) onDecision;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final name = approval['tool_name'] as String? ?? approval['name'] as String? ?? 'tool';
    final input = approval['input'];
    final inputText = input is Map ? const JsonEncoder.withIndent('  ').convert(input) : input?.toString() ?? '';

    return AppScaffold(
      title: Text(l10n.projectApproveTool),
      body: Padding(
        padding: EdgeInsets.all(AppSpacing.md),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(l10n.projectApproveToolPrompt(name)),
            SizedBox(height: AppSpacing.md),
            Text(l10n.projectToolApprovalHint),
            if (inputText.isNotEmpty) ...[
              SizedBox(height: AppSpacing.md),
              Expanded(
                child: SingleChildScrollView(
                  child: SelectableText(inputText),
                ),
              ),
            ] else
              const Spacer(),
            Row(
              children: [
                Expanded(
                  child: OutlinedButton(
                    onPressed: () async {
                      await onDecision('deny');
                      if (context.mounted) Navigator.of(context).pop();
                    },
                    child: Text(l10n.projectDeny),
                  ),
                ),
                SizedBox(width: AppSpacing.sm),
                Expanded(
                  child: FilledButton(
                    onPressed: () async {
                      await onDecision('approve');
                      if (context.mounted) Navigator.of(context).pop();
                    },
                    child: Text(l10n.projectApproveAndContinue),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
