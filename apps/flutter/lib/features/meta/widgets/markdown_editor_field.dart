import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_multiline_text_field.dart';

/// Inline multiline markdown editor (meta form widget `markdown_editor`).
class MarkdownEditorField extends StatelessWidget {
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
                child: Text(label, style: theme.textTheme.titleSmall),
              ),
              if (onUploadMarkdown != null && !readOnly)
                TextButton.icon(
                  onPressed: () async {
                    await onUploadMarkdown!(value);
                  },
                  icon: const Icon(Icons.upload_file_outlined, size: 18),
                  label: const Text('.md'),
                ),
            ],
          ),
          const SizedBox(height: AppSpacing.xs),
          AppMultilineTextField(
            value: value,
            readOnly: readOnly,
            markdown: true,
            minLines: 8,
            maxLines: 16,
            onChanged: onChanged,
          ),
        ],
      ),
    );
  }
}
