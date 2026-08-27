import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'package:prodavan/core/preferences/app_preference_tile.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_snack_bar.dart';
import 'package:prodavan/core/widgets/app_trailing_chevron.dart';

/// Borderless inline [TextField] decoration (Hiddify-style).
const kBorderlessInputDecoration = InputDecoration(
  isDense: true,
  isCollapsed: true,
  filled: false,
  border: InputBorder.none,
  enabledBorder: InputBorder.none,
  focusedBorder: InputBorder.none,
  disabledBorder: InputBorder.none,
  errorBorder: InputBorder.none,
  focusedErrorBorder: InputBorder.none,
  contentPadding: EdgeInsets.zero,
);

/// Text/number/password preference with inline edit and seamless save on commit.
class AppValuePreference<T> extends StatefulWidget {
  const AppValuePreference({
    super.key,
    required this.title,
    required this.value,
    required this.onSave,
    this.enabled = true,
    this.icon,
    this.obscureText = false,
    this.digitsOnly = false,
    this.hintText,
    this.invalidMessage,
    this.presentValue,
    this.formatInputValue,
    this.validateInput,
    this.inputToValue,
    this.keyboardType,
    this.maxLines = 1,
    this.onTap,
  });

  final String title;
  final T value;
  final Future<void> Function(T value) onSave;
  final bool enabled;
  /// When set, tap invokes this instead of inline edit (e.g. copy read-only ID).
  final VoidCallback? onTap;
  final IconData? icon;
  final bool obscureText;
  final bool digitsOnly;
  final String? hintText;
  /// Shown in a snackbar when [validateInput] rejects the value on save.
  final String? invalidMessage;
  final String Function(T value)? presentValue;
  final String Function(T value)? formatInputValue;
  final bool Function(String raw)? validateInput;
  final T? Function(String raw)? inputToValue;
  final TextInputType? keyboardType;
  final int maxLines;

  @override
  State<AppValuePreference<T>> createState() => _AppValuePreferenceState<T>();
}

class _AppValuePreferenceState<T> extends State<AppValuePreference<T>> {
  late TextEditingController _controller;
  late FocusNode _focusNode;
  bool _expanded = false;
  bool _obscured = true;
  bool _saving = false;
  bool _startedBlank = false;
  bool _ignoreNextBlur = false;

  String _display(T val) =>
      widget.presentValue?.call(val) ?? val.toString();

  String _editText(T val) =>
      widget.formatInputValue?.call(val) ?? val.toString();

  @override
  void initState() {
    super.initState();
    _controller = TextEditingController(text: _editText(widget.value));
    _focusNode = FocusNode()..addListener(_onFocusChange);
  }

  @override
  void didUpdateWidget(covariant AppValuePreference<T> oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!_expanded) {
      _controller.text = _editText(widget.value);
    }
  }

  @override
  void dispose() {
    _focusNode.removeListener(_onFocusChange);
    _focusNode.dispose();
    _controller.dispose();
    super.dispose();
  }

  void _onFocusChange() {
    if (_focusNode.hasFocus || !_expanded || _saving) return;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || _focusNode.hasFocus || !_expanded || _saving) return;
      if (_ignoreNextBlur) {
        _ignoreNextBlur = false;
        return;
      }
      final raw = _controller.text.trim();
      if (_startedBlank && raw.isNotEmpty) {
        _save();
        return;
      }
      _cancel();
    });
  }

  void _cancel() {
    setState(() {
      _expanded = false;
      _controller.text = _editText(widget.value);
    });
    _focusNode.unfocus();
  }

  Future<void> _save() async {
    if (_saving || !widget.enabled) return;
    final raw = _controller.text.trim();
    if (widget.validateInput != null && !widget.validateInput!(raw)) {
      final msg = widget.invalidMessage;
      if (msg != null && msg.isNotEmpty && mounted) {
        AppSnackBar.error(context, msg, copyOnTap: false);
      }
      return;
    }
    final parsed = widget.inputToValue != null
        ? widget.inputToValue!(raw)
        : raw as T?;
    if (parsed == null) return;
    setState(() => _saving = true);
    try {
      await widget.onSave(parsed);
      if (!mounted) return;
      setState(() {
        _expanded = false;
        _saving = false;
      });
      _focusNode.unfocus();
    } catch (e) {
      if (mounted) {
        setState(() => _saving = false);
        AppErrors.showSnack(context, e);
      }
    }
  }

  void _handleTap() {
    if (widget.onTap != null) {
      widget.onTap!();
      return;
    }
    _beginEdit();
  }

  void _beginEdit() {
    if (!widget.enabled || _expanded) return;
    _controller.text = _editText(widget.value);
    _startedBlank = _editText(widget.value).isEmpty;
    setState(() => _expanded = true);
    WidgetsBinding.instance.addPostFrameCallback((_) => _focusNode.requestFocus());
  }

  void _guardBlur() => _ignoreNextBlur = true;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isBlank = _editText(widget.value).isEmpty;
    final subtitleText = widget.obscureText && !_expanded && !isBlank
        ? '••••••••'
        : _display(widget.value);

    if (_expanded) {
      return AppPreferenceTile(
        title: widget.title,
        icon: widget.icon,
        enabled: widget.enabled && !_saving,
        subtitle: TextField(
          controller: _controller,
          focusNode: _focusNode,
          autofocus: true,
          obscureText: widget.obscureText && _obscured,
          maxLines: widget.maxLines,
          keyboardType: widget.digitsOnly
              ? TextInputType.number
              : (widget.keyboardType ?? TextInputType.text),
          inputFormatters: widget.digitsOnly
              ? [FilteringTextInputFormatter.digitsOnly]
              : null,
          textInputAction: TextInputAction.done,
          style: theme.textTheme.bodyMedium?.copyWith(
            fontFamily: widget.digitsOnly ? 'monospace' : null,
          ),
          decoration: kBorderlessInputDecoration.copyWith(hintText: widget.hintText),
          onSubmitted: (_) => _save(),
        ),
        trailing: AppPreferenceInlineActions(
          onSave: _save,
          onCancel: _cancel,
          onGuardBlur: _guardBlur,
          onToggleObscure: widget.obscureText
              ? () => setState(() => _obscured = !_obscured)
              : null,
          obscured: _obscured,
        ),
      );
    }

    return AppPreferenceTile(
      title: widget.title,
      icon: widget.icon,
      enabled: widget.enabled || widget.onTap != null,
      subtitle: Text(subtitleText, style: theme.textTheme.bodyMedium),
      trailing: widget.onTap != null
          ? Icon(Icons.copy_outlined, size: 20, color: theme.colorScheme.onSurfaceVariant)
          : const AppTrailingChevron(),
      onTap: _handleTap,
    );
  }
}
