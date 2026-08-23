import 'dart:convert';

import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';

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
  String? _error;

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
        _error = e.toString();
        _busy = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final inputPreview = const JsonEncoder.withIndent('  ').convert(widget.toolInput);
    return AppScaffold(
      title: const Text('Approve tool'),
      body: Padding(
        padding: const EdgeInsets.all(AppSpacing.lg),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (_error != null) InlineErrorBanner(message: _error!),
            Text(
              widget.toolName,
              style: Theme.of(context).textTheme.titleLarge,
            ),
            const SizedBox(height: AppSpacing.sm),
            Text(
              'This tool requires human approval before the agent can continue.',
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
              label: 'Deny',
              variant: AppButtonVariant.outlined,
              onPressed: _busy ? null : () => _decide('deny'),
            ),
            const SizedBox(height: AppSpacing.sm),
            AppButton(
              label: _busy ? 'Working…' : 'Approve and continue',
              onPressed: _busy ? null : () => _decide('approve'),
            ),
          ],
        ),
      ),
    );
  }
}
