import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'package:prodavan/core/theme/app_spacing.dart';

/// Multiline JSON editor with lightweight syntax highlighting and validation.
class AppJsonEditorField extends StatefulWidget {
  const AppJsonEditorField({
    super.key,
    required this.controller,
    this.onChanged,
    this.validator,
    this.minLines = 14,
    this.maxLines = 40,
    this.readOnly = false,
  });

  final TextEditingController controller;
  final ValueChanged<String>? onChanged;
  final String? Function(Object? parsed)? validator;
  final int minLines;
  final int maxLines;
  final bool readOnly;

  @override
  State<AppJsonEditorField> createState() => AppJsonEditorFieldState();
}

class AppJsonEditorFieldState extends State<AppJsonEditorField> {
  final _focusNode = FocusNode();
  String? _parseError;
  String? _domainError;

  @override
  void initState() {
    super.initState();
    widget.controller.addListener(_handleChange);
    _validate(widget.controller.text);
  }

  @override
  void didUpdateWidget(covariant AppJsonEditorField oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.controller != widget.controller) {
      oldWidget.controller.removeListener(_handleChange);
      widget.controller.addListener(_handleChange);
      _validate(widget.controller.text);
    }
  }

  @override
  void dispose() {
    widget.controller.removeListener(_handleChange);
    _focusNode.dispose();
    super.dispose();
  }

  void _handleChange() {
    _validate(widget.controller.text);
    widget.onChanged?.call(widget.controller.text);
  }

  void _validate(String text) {
    String? parseError;
    String? domainError;
    Object? parsed;
    if (text.trim().isEmpty) {
      parseError = null;
    } else {
      try {
        parsed = jsonDecode(text);
      } catch (e) {
        parseError = e.toString();
      }
    }
    if (parseError == null && parsed != null && widget.validator != null) {
      domainError = widget.validator!(parsed);
    }
    if (parseError != _parseError || domainError != _domainError) {
      setState(() {
        _parseError = parseError;
        _domainError = domainError;
      });
    }
  }

  bool get isValidJson => _parseError == null && widget.controller.text.trim().isNotEmpty;

  bool get isFullyValid => isValidJson && _domainError == null;

  Object? get parsedValue {
    if (!isValidJson) return null;
    return jsonDecode(widget.controller.text);
  }

  String? get errorText => _parseError ?? _domainError;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final hasError = errorText != null && widget.controller.text.trim().isNotEmpty;
    final borderColor = hasError ? theme.colorScheme.error : theme.dividerColor;
    final baseStyle = theme.textTheme.bodyMedium?.copyWith(
      fontFamily: 'monospace',
      fontSize: 13,
      height: 1.45,
    );

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Container(
          height: widget.minLines * 20.0 + 24,
          decoration: BoxDecoration(
            border: Border.all(color: borderColor),
            borderRadius: BorderRadius.circular(AppSpacing.sm),
            color: theme.colorScheme.surfaceContainerLowest,
          ),
          padding: const EdgeInsets.symmetric(
            horizontal: AppSpacing.sm,
            vertical: AppSpacing.xs,
          ),
          child: Stack(
            fit: StackFit.expand,
            children: [
              Padding(
                padding: const EdgeInsets.only(top: AppSpacing.xs),
                child: RichText(
                  text: _JsonHighlight.build(
                    widget.controller.text,
                    baseStyle ?? const TextStyle(fontSize: 13, height: 1.45),
                    theme,
                  ),
                ),
              ),
              TextField(
                controller: widget.controller,
                focusNode: _focusNode,
                readOnly: widget.readOnly,
                maxLines: null,
                expands: true,
                style: baseStyle?.copyWith(color: Colors.transparent),
                cursorColor: theme.colorScheme.primary,
                decoration: const InputDecoration(
                  border: InputBorder.none,
                  isDense: true,
                  contentPadding: EdgeInsets.zero,
                ),
                keyboardType: TextInputType.multiline,
                autocorrect: false,
                enableSuggestions: false,
                inputFormatters: [LengthLimitingTextInputFormatter(200000)],
              ),
            ],
          ),
        ),
        if (hasError) ...[
          const SizedBox(height: AppSpacing.xs),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md),
            child: Text(
              errorText!,
              style: theme.textTheme.bodySmall?.copyWith(
                color: theme.colorScheme.error,
              ),
            ),
          ),
        ],
      ],
    );
  }
}

abstract final class _JsonHighlight {
  static final _token = RegExp(
    r'"(?:\\.|[^"\\])*"|\btrue\b|\bfalse\b|\bnull\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\{|}|\[|\]|,|:',
  );

  static TextSpan build(String text, TextStyle base, ThemeData theme) {
    if (text.isEmpty) return TextSpan(text: ' ', style: base);
    final keyColor = theme.colorScheme.primary;
    final stringColor = theme.colorScheme.tertiary;
    final numberColor = theme.colorScheme.secondary;
    final literalColor = theme.colorScheme.error;
    final spans = <TextSpan>[];
    var index = 0;
    for (final match in _token.allMatches(text)) {
      if (match.start > index) {
        spans.add(TextSpan(text: text.substring(index, match.start), style: base));
      }
      final value = match.group(0)!;
      TextStyle style = base;
      if (value.startsWith('"')) {
        final after = text.indexOf(':', match.end);
        final nextNonWs = _nextNonWhitespace(text, match.end);
        final isKey = after != -1 && nextNonWs == after;
        style = base.copyWith(color: isKey ? keyColor : stringColor);
      } else if (value == 'true' || value == 'false' || value == 'null') {
        style = base.copyWith(color: literalColor);
      } else if (RegExp(r'^-?\d').hasMatch(value)) {
        style = base.copyWith(color: numberColor);
      }
      spans.add(TextSpan(text: value, style: style));
      index = match.end;
    }
    if (index < text.length) {
      spans.add(TextSpan(text: text.substring(index), style: base));
    }
    return TextSpan(children: spans);
  }

  static int _nextNonWhitespace(String text, int start) {
    for (var i = start; i < text.length; i++) {
      if (!RegExp(r'\s').hasMatch(text[i])) return i;
    }
    return -1;
  }
}
