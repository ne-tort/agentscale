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
  static const _lineHeight = 1.45;
  static const _fontSize = 13.0;

  final _focusNode = FocusNode();
  final _scrollController = ScrollController();
  String? _parseError;
  String? _domainError;

  @override
  void initState() {
    super.initState();
    widget.controller.addListener(_handleChange);
    _scrollController.addListener(_onScroll);
    _validate(widget.controller.text);
  }

  @override
  void didUpdateWidget(covariant AppJsonEditorField oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.controller != widget.controller) {
      oldWidget.controller.removeListener(_handleChange);
      widget.controller.addListener(_handleChange);
      _validate(widget.controller.text);
      setState(() {});
    }
  }

  @override
  void dispose() {
    widget.controller.removeListener(_handleChange);
    _scrollController.removeListener(_onScroll);
    _scrollController.dispose();
    _focusNode.dispose();
    super.dispose();
  }

  void _onScroll() {
    setState(() {});
  }

  void _handleChange() {
    _validate(widget.controller.text);
    widget.onChanged?.call(widget.controller.text);
    setState(() {});
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
    _parseError = parseError;
    _domainError = domainError;
  }

  bool get isValidJson => _parseError == null && widget.controller.text.trim().isNotEmpty;

  bool get isFullyValid => isValidJson && _domainError == null;

  Object? get parsedValue {
    if (!isValidJson) return null;
    return jsonDecode(widget.controller.text);
  }

  String? get errorText => _parseError ?? _domainError;

  TextStyle _baseStyle(ThemeData theme) {
    return theme.textTheme.bodyMedium?.copyWith(
          fontFamily: 'monospace',
          fontSize: _fontSize,
          height: _lineHeight,
        ) ??
        const TextStyle(fontFamily: 'monospace', fontSize: _fontSize, height: _lineHeight);
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final hasError = errorText != null && widget.controller.text.trim().isNotEmpty;
    final baseStyle = _baseStyle(theme);
    final editorHeight = widget.minLines * (_fontSize * _lineHeight) + AppSpacing.sm;
    const fieldPadding = EdgeInsets.symmetric(
      horizontal: AppSpacing.sm,
      vertical: AppSpacing.xs,
    );
    final scrollOffset = _scrollController.hasClients ? _scrollController.offset : 0.0;

    Widget editor = SizedBox(
      height: editorHeight,
      child: Stack(
        fit: StackFit.expand,
        children: [
          Padding(
            padding: fieldPadding,
            child: ClipRect(
              child: Transform.translate(
                offset: Offset(0, -scrollOffset),
                child: RichText(
                  text: _JsonHighlight.build(
                    widget.controller.text,
                    baseStyle,
                    theme,
                  ),
                ),
              ),
            ),
          ),
          Padding(
            padding: fieldPadding,
            child: TextField(
              controller: widget.controller,
              focusNode: _focusNode,
              scrollController: _scrollController,
              readOnly: widget.readOnly,
              maxLines: null,
              expands: true,
              style: baseStyle.copyWith(color: Colors.transparent),
              cursorColor: theme.colorScheme.primary,
              scrollPadding: EdgeInsets.zero,
              decoration: const InputDecoration(
                border: InputBorder.none,
                enabledBorder: InputBorder.none,
                focusedBorder: InputBorder.none,
                disabledBorder: InputBorder.none,
                errorBorder: InputBorder.none,
                focusedErrorBorder: InputBorder.none,
                isDense: true,
                contentPadding: EdgeInsets.zero,
                filled: false,
              ),
              keyboardType: TextInputType.multiline,
              autocorrect: false,
              enableSuggestions: false,
              inputFormatters: [LengthLimitingTextInputFormatter(200000)],
            ),
          ),
        ],
      ),
    );

    if (hasError) {
      editor = DecoratedBox(
        decoration: BoxDecoration(
          border: Border.all(color: theme.colorScheme.error),
          borderRadius: BorderRadius.circular(AppSpacing.sm),
        ),
        child: editor,
      );
    }

    editor = Theme(
      data: theme.copyWith(
        focusColor: Colors.transparent,
        hoverColor: Colors.transparent,
        splashColor: Colors.transparent,
        highlightColor: Colors.transparent,
      ),
      child: editor,
    );

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        editor,
        if (hasError) ...[
          const SizedBox(height: AppSpacing.xs),
          Text(
            errorText!,
            style: theme.textTheme.bodySmall?.copyWith(
              color: theme.colorScheme.error,
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
