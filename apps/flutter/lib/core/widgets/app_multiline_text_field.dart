import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_markdown_editing_controller.dart';

/// Shared multiline editor surface (plain or Markdown-highlighted).
///
/// Used by full-page editors and inline meta fields so styling stays one place.
class AppMultilineTextField extends StatefulWidget {
  const AppMultilineTextField({
    super.key,
    required this.value,
    required this.onChanged,
    this.readOnly = false,
    this.markdown = false,
    this.minLines,
    this.maxLines,
    this.expands = false,
    this.focusNode,
    this.onEditingComplete,
    this.hintText,
    this.labelText,
  });

  final String value;
  final ValueChanged<String> onChanged;
  final bool readOnly;
  final bool markdown;
  final int? minLines;
  final int? maxLines;
  final bool expands;
  final FocusNode? focusNode;
  final VoidCallback? onEditingComplete;
  final String? hintText;
  final String? labelText;

  @override
  State<AppMultilineTextField> createState() => _AppMultilineTextFieldState();
}

class _AppMultilineTextFieldState extends State<AppMultilineTextField> {
  late TextEditingController _controller;
  FocusNode? _ownedFocus;
  FocusNode get _focus => widget.focusNode ?? _ownedFocus!;

  @override
  void initState() {
    super.initState();
    _controller = widget.markdown
        ? AppMarkdownEditingController(text: widget.value)
        : TextEditingController(text: widget.value);
    if (widget.focusNode == null) {
      _ownedFocus = FocusNode();
    }
    _focus.addListener(_onFocusChange);
  }

  @override
  void didUpdateWidget(covariant AppMultilineTextField oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.markdown != widget.markdown) {
      final text = _controller.text;
      _controller.dispose();
      _controller = widget.markdown
          ? AppMarkdownEditingController(text: text)
          : TextEditingController(text: text);
    }
    if (oldWidget.value != widget.value && _controller.text != widget.value) {
      _controller.value = TextEditingValue(
        text: widget.value,
        selection: TextSelection.collapsed(offset: widget.value.length),
      );
    }
  }

  @override
  void dispose() {
    _focus.removeListener(_onFocusChange);
    _ownedFocus?.dispose();
    _controller.dispose();
    super.dispose();
  }

  void _onFocusChange() {
    if (!_focus.hasFocus) {
      widget.onEditingComplete?.call();
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return TextField(
      controller: _controller,
      focusNode: _focus,
      readOnly: widget.readOnly,
      expands: widget.expands,
      maxLines: widget.expands ? null : widget.maxLines,
      minLines: widget.expands ? null : widget.minLines,
      textAlignVertical: TextAlignVertical.top,
      style: theme.textTheme.bodyMedium?.copyWith(
        fontFamily: 'monospace',
        height: 1.45,
      ),
      decoration: InputDecoration(
        labelText: widget.labelText,
        hintText: widget.hintText,
        alignLabelWithHint: true,
        border: const OutlineInputBorder(),
        filled: true,
        fillColor: theme.colorScheme.surfaceContainerHighest.withValues(alpha: 0.35),
        contentPadding: const EdgeInsets.all(AppSpacing.md),
      ),
      onChanged: widget.onChanged,
    );
  }
}
