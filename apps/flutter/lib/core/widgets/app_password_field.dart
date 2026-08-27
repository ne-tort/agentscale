import 'package:flutter/material.dart';

import 'package:prodavan/core/widgets/app_icon_button.dart';
import 'package:prodavan/core/widgets/app_text_field.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Password input with visibility toggle — wraps [AppTextField].
class AppPasswordField extends StatefulWidget {
  const AppPasswordField({
    super.key,
    this.controller,
    this.focusNode,
    this.label,
    this.hint,
    this.validator,
    this.enabled = true,
    this.textInputAction,
    this.onChanged,
    this.onFieldSubmitted,
    this.autofillHints = const [AutofillHints.password],
    this.size = AppFieldSize.regular,
  });

  final TextEditingController? controller;
  final FocusNode? focusNode;
  final String? label;
  final String? hint;
  final FormFieldValidator<String>? validator;
  final bool enabled;
  final TextInputAction? textInputAction;
  final ValueChanged<String>? onChanged;
  final ValueChanged<String>? onFieldSubmitted;
  final Iterable<String>? autofillHints;
  final AppFieldSize size;

  @override
  State<AppPasswordField> createState() => _AppPasswordFieldState();
}

class _AppPasswordFieldState extends State<AppPasswordField> {
  bool _obscure = true;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return AppTextField(
      controller: widget.controller,
      focusNode: widget.focusNode,
      label: widget.label ?? l10n.authPassword,
      hint: widget.hint,
      validator: widget.validator,
      enabled: widget.enabled,
      obscureText: _obscure,
      textInputAction: widget.textInputAction,
      onChanged: widget.onChanged,
      onFieldSubmitted: widget.onFieldSubmitted,
      autofillHints: widget.autofillHints,
      size: widget.size,
      suffixIcon: AppIconButton(
        icon: _obscure ? Icons.visibility_outlined : Icons.visibility_off_outlined,
        tooltip: _obscure ? l10n.authShowPassword : l10n.authHidePassword,
        onPressed: widget.enabled
            ? () => setState(() => _obscure = !_obscure)
            : null,
      ),
    );
  }
}
