import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/l10n/app_localizations.dart';

class ChatComposer extends StatefulWidget {
  const ChatComposer({
    super.key,
    required this.onSend,
    this.enabled = true,
    this.disabledHint,
    this.onCancel,
  });

  final ValueChanged<String> onSend;
  final bool enabled;
  final String? disabledHint;
  final VoidCallback? onCancel;

  @override
  State<ChatComposer> createState() => _ChatComposerState();
}

class _ChatComposerState extends State<ChatComposer> {
  final _controller = TextEditingController();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  void _submit() {
    final text = _controller.text;
    if (text.trim().isEmpty) return;
    widget.onSend(text);
    _controller.clear();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return SafeArea(
      top: false,
      child: Padding(
        padding: EdgeInsets.all(AppSpacing.md),
        child: Row(
          children: [
            Expanded(
              child: TextField(
                controller: _controller,
                enabled: widget.enabled,
                minLines: 1,
                maxLines: 6,
                decoration: InputDecoration(
                  hintText: widget.enabled
                      ? l10n.projectMessageHint
                      : (widget.disabledHint ?? l10n.projectMessageHint),
                  border: const OutlineInputBorder(),
                ),
                onSubmitted: widget.enabled ? (_) => _submit() : null,
              ),
            ),
            SizedBox(width: AppSpacing.sm),
            if (widget.onCancel != null)
              IconButton(
                tooltip: l10n.commonCancel,
                onPressed: widget.onCancel,
                icon: const Icon(Icons.stop_circle_outlined),
              )
            else
              IconButton(
                tooltip: l10n.commonContinueAction,
                onPressed: widget.enabled ? _submit : null,
                icon: const Icon(Icons.send),
              ),
          ],
        ),
      ),
    );
  }
}
