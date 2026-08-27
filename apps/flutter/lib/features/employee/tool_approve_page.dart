import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Full-page HITL tool approval (L05/L08 — no modals).
class ToolApprovePage extends StatefulWidget {
  const ToolApprovePage({
    super.key,
    required this.projectId,
    required this.sessionId,
    required this.approvalId,
    required this.toolName,
    this.toolInput = const {},
  });

  final String projectId;
  final String sessionId;
  final String approvalId;
  final String toolName;
  final Map<String, dynamic> toolInput;

  @override
  State<ToolApprovePage> createState() => _ToolApprovePageState();
}

class _ToolApprovePageState extends State<ToolApprovePage> {
  bool _busy = false;

  Future<void> _decide(String decision) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      await workContext.api.resolveToolApproval(
        projectId: widget.projectId,
        sessionId: widget.sessionId,
        approvalId: widget.approvalId,
        decision: decision,
      );
      if (!mounted) return;
      Navigator.of(context).pop(decision);
    } catch (e) {
      if (!mounted) return;
      setState(() => _busy = false);
      AppErrors.showSnack(context, e);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final inputPreview = JsonEncoder.withIndent('  ').convert(widget.toolInput);
    return AppScaffold(
      title: Text(l10n.projectApproveTool),
      body: ListView(
        children: [
          Padding(
            padding: const EdgeInsets.all(AppSpacing.lg),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(
                  widget.toolName,
                  style: Theme.of(context).textTheme.titleLarge,
                ),
                const SizedBox(height: AppSpacing.sm),
                Text(
                  l10n.projectToolApprovalHint,
                  style: Theme.of(context).textTheme.bodyMedium,
                ),
                const SizedBox(height: AppSpacing.md),
                SelectableText(
                  inputPreview,
                  style: Theme.of(context).textTheme.bodySmall?.copyWith(
                        fontFamily: 'monospace',
                      ),
                ),
              ],
            ),
          ),
          if (_busy)
            const Padding(
              padding: EdgeInsets.all(AppSpacing.lg),
              child: Center(child: CircularProgressIndicator(strokeWidth: 2)),
            )
          else ...[
            AppNavPreference(
              title: l10n.projectDeny,
              icon: Icons.block_rounded,
              accentColor: context.appColors.danger,
              onTap: () => _decide('deny'),
            ),
            AppNavPreference(
              title: l10n.projectApproveAndContinue,
              icon: Icons.check_circle_outline_rounded,
              onTap: () => _decide('approve'),
            ),
          ],
        ],
      ),
    );
  }
}
