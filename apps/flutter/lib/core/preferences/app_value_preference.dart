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
    this.accentColor,
    this.busy = false,
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
  final Color? accentColor;
  /// External busy (e.g. remote DSN probe) — spinner on the tile.
  final bool busy;

  @override
  State<AppValuePreference<T>> createState() => _AppValuePreferenceState<T>();
}

class _AppValuePreferenceState<T> extends State<AppValuePreference<T>> {
  late TextEditingController _controller;
  late FocusNode _focusNode;
  bool _expanded = false;
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
    if (_saving || !widget.enabled || widget.busy) return;
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
    if (!widget.enabled || _expanded || widget.busy || _saving) return;
    _controller.text = _editText(widget.value);
    _startedBlank = _editText(widget.value).isEmpty;
    setState(() => _expanded = true);
    WidgetsBinding.instance.addPostFrameCallback((_) => _focusNode.requestFocus());
  }

  void _guardBlur() => _ignoreNextBlur = true;

  Widget _busyTrailing(ThemeData theme) {
    return SizedBox(
      width: 20,
      height: 20,
      child: CircularProgressIndicator(
        strokeWidth: 2,
        color: widget.accentColor ?? theme.colorScheme.primary,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isBlank = _editText(widget.value).isEmpty;
    final showBusy = widget.busy || _saving;

    if (_expanded && !showBusy) {
      return AppPreferenceTile(
        title: widget.title,
        icon: widget.icon,
        accentColor: widget.accentColor,
        enabled: widget.enabled && !_saving,
        subtitle: TextField(
          controller: _controller,
          focusNode: _focusNode,
          autofocus: true,
          obscureText: widget.obscureText,
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
            color: widget.accentColor,
          ),
          decoration: kBorderlessInputDecoration.copyWith(hintText: widget.hintText),
          onSubmitted: (_) => _save(),
        ),
        trailing: AppPreferenceInlineActions(
          onSave: _save,
          onCancel: _cancel,
          onGuardBlur: _guardBlur,
        ),
      );
    }

    // Blank value → no subtitle slot (not empty Text / "Not set").
    final Widget? subtitle = isBlank
        ? null
        : Text(
            widget.obscureText ? '••••••••' : _display(widget.value),
            style: theme.textTheme.bodyMedium?.copyWith(color: widget.accentColor),
          );

    return AppPreferenceTile(
      title: widget.title,
      icon: widget.icon,
      accentColor: widget.accentColor,
      enabled: (widget.enabled || widget.onTap != null) && !showBusy,
      subtitle: subtitle,
      trailing: showBusy
          ? _busyTrailing(theme)
          : widget.onTap != null
              ? Icon(
                  Icons.copy_outlined,
                  size: 20,
                  color: widget.accentColor ?? theme.colorScheme.onSurfaceVariant,
                )
              : const AppTrailingChevron(),
      onTap: showBusy ? null : _handleTap,
    );
  }
}
