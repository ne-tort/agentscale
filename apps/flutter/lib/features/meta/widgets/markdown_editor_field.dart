import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_spacing.dart';

/// Multiline markdown editor with monospace styling (syntax-friendly).
class MarkdownEditorField extends StatefulWidget {
  const MarkdownEditorField({
    super.key,
    required this.label,
    required this.value,
    required this.onChanged,
    this.readOnly = false,
    this.onUploadMarkdown,
  });

  final String label;
  final String value;
  final ValueChanged<String> onChanged;
  final bool readOnly;
  final Future<void> Function(String text)? onUploadMarkdown;

  @override
  State<MarkdownEditorField> createState() => _MarkdownEditorFieldState();
}

class _MarkdownEditorFieldState extends State<MarkdownEditorField> {
  late TextEditingController _controller;

  @override
  void initState() {
    super.initState();
    _controller = TextEditingController(text: widget.value);
  }

  @override
  void didUpdateWidget(covariant MarkdownEditorField oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.value != widget.value && _controller.text != widget.value) {
      _controller.text = widget.value;
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.md,
        vertical: AppSpacing.sm,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(widget.label, style: theme.textTheme.titleSmall),
              ),
              if (widget.onUploadMarkdown != null && !widget.readOnly)
                TextButton.icon(
                  onPressed: () async {
                    await widget.onUploadMarkdown!(_controller.text);
                  },
                  icon: const Icon(Icons.upload_file_outlined, size: 18),
                  label: const Text('.md'),
                ),
            ],
          ),
          const SizedBox(height: AppSpacing.xs),
          TextField(
            controller: _controller,
            readOnly: widget.readOnly,
            maxLines: 16,
            minLines: 8,
            style: theme.textTheme.bodyMedium?.copyWith(
              fontFamily: 'monospace',
              height: 1.45,
            ),
            decoration: InputDecoration(
              border: const OutlineInputBorder(),
              filled: true,
              fillColor: theme.colorScheme.surfaceContainerHighest.withValues(alpha: 0.35),
            ),
            onChanged: widget.onChanged,
          ),
        ],
      ),
    );
  }
}
