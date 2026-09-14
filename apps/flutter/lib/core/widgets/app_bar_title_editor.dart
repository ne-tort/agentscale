import 'dart:async';

import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/app_value_preference.dart';

/// Borderless AppBar title that becomes editable on tap; saves on focus loss.
class AppBarTitleEditor extends StatefulWidget {
  const AppBarTitleEditor({
    super.key,
    required this.value,
    required this.hintText,
    required this.onSave,
  });

  final String value;
  final String hintText;
  final Future<void> Function(String value) onSave;

  @override
  State<AppBarTitleEditor> createState() => _AppBarTitleEditorState();
}

class _AppBarTitleEditorState extends State<AppBarTitleEditor> {
  late final TextEditingController _controller;
  late final FocusNode _focus;
  bool _editing = false;
  bool _saving = false;

  @override
  void initState() {
    super.initState();
    _controller = TextEditingController(text: widget.value);
    _focus = FocusNode();
    _focus.addListener(_onFocusChange);
  }

  @override
  void didUpdateWidget(covariant AppBarTitleEditor oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!_editing && oldWidget.value != widget.value) {
      _controller.text = widget.value;
    }
  }

  @override
  void dispose() {
    _focus.removeListener(_onFocusChange);
    _focus.dispose();
    _controller.dispose();
    super.dispose();
  }

  void _onFocusChange() {
    if (!_focus.hasFocus && _editing) {
      unawaited(_commit());
    }
  }

  Future<void> _beginEdit() async {
    if (_saving) return;
    setState(() {
      _editing = true;
      _controller.text = widget.value;
      _controller.selection = TextSelection(
        baseOffset: 0,
        extentOffset: _controller.text.length,
      );
    });
    await Future<void>.delayed(Duration.zero);
    if (mounted) _focus.requestFocus();
  }

  Future<void> _commit() async {
    if (_saving) return;
    final next = _controller.text.trim();
    setState(() {
      _editing = false;
      _saving = true;
    });
    try {
      await widget.onSave(next);
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final style = theme.textTheme.titleLarge ?? theme.textTheme.titleMedium;
    if (_editing) {
      return TextField(
        controller: _controller,
        focusNode: _focus,
        style: style,
        cursorColor: theme.colorScheme.primary,
        textInputAction: TextInputAction.done,
        decoration: kBorderlessInputDecoration.copyWith(
          hintText: widget.hintText,
          isDense: true,
          contentPadding: EdgeInsets.zero,
        ),
        onSubmitted: (_) => unawaited(_commit()),
      );
    }
    final display = widget.value.trim().isEmpty ? widget.hintText : widget.value;
    return GestureDetector(
      onTap: () => unawaited(_beginEdit()),
      behavior: HitTestBehavior.opaque,
      child: Text(
        display,
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style: style,
      ),
    );
  }
}
