import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
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
  Object? _error;

  Future<void> _decide(String decision) async {
    setState(() {
      _busy = true;
      _error = null;
    });
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
      setState(() {
        _error = e;
        _busy = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final inputPreview = JsonEncoder.withIndent('  ').convert(widget.toolInput);
    return AppScaffold(
      title: Text(l10n.projectApproveTool),
      body: Padding(
        padding: const EdgeInsets.all(AppSpacing.lg),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (_error != null) AppStatusBanner(severity: AppStatusSeverity.error, message: AppErrors.localize(context, _error!)),
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
            Expanded(
              child: SingleChildScrollView(
                child: SelectableText(
                  inputPreview,
                  style: Theme.of(context).textTheme.bodySmall?.copyWith(fontFamily: 'monospace'),
                ),
              ),
            ),
            AppButton(
              label: l10n.projectDeny,
              variant: AppButtonVariant.outlined,
              onPressed: _busy ? null : () => _decide('deny'),
            ),
            const SizedBox(height: AppSpacing.sm),
            AppButton(
              label: _busy ? l10n.projectWorking : l10n.projectApproveAndContinue,
              onPressed: _busy ? null : () => _decide('approve'),
            ),
          ],
        ),
      ),
    );
  }
}
