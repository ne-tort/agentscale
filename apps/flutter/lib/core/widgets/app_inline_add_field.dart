import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/app_preference_tile.dart';
import 'package:prodavan/core/preferences/app_value_preference.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Expand-to-edit add field (Hiddify clients style) with inline + button.
class AppInlineAddField extends StatefulWidget {
  const AppInlineAddField({
    super.key,
    required this.title,
    required this.validator,
    required this.onSave,
    this.hintText,
    this.invalidMessage,
  });

  final String title;
  final bool Function(String raw) validator;
  final Future<void> Function(String raw) onSave;
  final String? hintText;
  final String? invalidMessage;

  @override
  State<AppInlineAddField> createState() => _AppInlineAddFieldState();
}

class _AppInlineAddFieldState extends State<AppInlineAddField> {
  final _controller = TextEditingController();
  final _focusNode = FocusNode();
  bool _expanded = false;
  bool _saving = false;
  bool _showError = false;

  @override
  void initState() {
    super.initState();
    _focusNode.addListener(_onFocusChange);
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
    if (_controller.text.trim().isEmpty) {
      _cancel();
    }
  }

  void _cancel() {
    if (_saving) return;
    setState(() {
      _expanded = false;
      _showError = false;
      _controller.clear();
    });
    _focusNode.unfocus();
  }

  void _beginEdit() {
    if (_saving) return;
    setState(() {
      _expanded = true;
      _showError = false;
    });
    WidgetsBinding.instance.addPostFrameCallback((_) => _focusNode.requestFocus());
  }

  Future<void> _save() async {
    if (_saving) return;
    final raw = _controller.text.trim();
    if (!widget.validator(raw)) {
      setState(() => _showError = true);
      return;
    }
    setState(() {
      _showError = false;
      _saving = true;
    });
    try {
      await widget.onSave(raw);
      if (!mounted) return;
      setState(() {
        _expanded = false;
        _saving = false;
      });
      _controller.clear();
      _focusNode.unfocus();
    } catch (e) {
      if (mounted) {
        setState(() => _saving = false);
        AppErrors.showSnack(context, e);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final l10n = AppLocalizations.of(context);

    if (_expanded) {
      return AppPreferenceTile(
        title: widget.title,
        enabled: !_saving,
        subtitle: TextField(
          controller: _controller,
          focusNode: _focusNode,
          autofocus: true,
          enabled: !_saving,
          textInputAction: TextInputAction.done,
          style: theme.textTheme.bodyMedium,
          decoration: kBorderlessInputDecoration.copyWith(
            hintText: widget.hintText,
            errorText: _showError ? widget.invalidMessage : null,
          ),
          onSubmitted: (_) => _save(),
          onChanged: (_) {
            if (_showError) setState(() => _showError = false);
          },
        ),
        trailing: _saving
            ? const Padding(
                padding: EdgeInsets.symmetric(horizontal: AppSpacing.sm),
                child: SizedBox(
                  width: 18,
                  height: 18,
                  child: CircularProgressIndicator(strokeWidth: 2),
                ),
              )
            : AppPreferenceInlineActions(
                onSave: _save,
                onCancel: _cancel,
                onGuardBlur: () {},
              ),
      );
    }

    return AppPreferenceTile(
      title: widget.title,
      enabled: !_saving,
      trailing: SizedBox(
        width: 48,
        child: Align(
          alignment: Alignment.centerRight,
          child: IconButton(
            tooltip: l10n.commonAdd,
            icon: const Icon(Icons.add_rounded, size: 22),
            visualDensity: VisualDensity.compact,
            onPressed: _saving ? null : _beginEdit,
          ),
        ),
      ),
      onTap: _saving ? null : _beginEdit,
    );
  }
}
