import 'package:flutter/material.dart';

enum AppFieldSize { compact, regular }

class AppTextField extends StatelessWidget {
  const AppTextField({
    super.key,
    this.controller,
    this.label,
    this.hint,
    this.validator,
    this.keyboardType,
    this.textInputAction,
    this.enabled = true,
    this.readOnly = false,
    this.obscureText = false,
    this.autofillHints,
    this.onChanged,
    this.prefixIcon,
    this.suffixIcon,
    this.maxLines = 1,
    this.size = AppFieldSize.regular,
  });

  final TextEditingController? controller;
  final String? label;
  final String? hint;
  final FormFieldValidator<String>? validator;
  final TextInputType? keyboardType;
  final TextInputAction? textInputAction;
  final bool enabled;
  final bool readOnly;
  final bool obscureText;
  final Iterable<String>? autofillHints;
  final ValueChanged<String>? onChanged;
  final Widget? prefixIcon;
  final Widget? suffixIcon;
  final int? maxLines;
  final AppFieldSize size;

  @override
  Widget build(BuildContext context) {
    return TextFormField(
      controller: controller,
      validator: validator,
      keyboardType: keyboardType,
      textInputAction: textInputAction,
      enabled: enabled,
      readOnly: readOnly,
      obscureText: obscureText,
      autofillHints: autofillHints,
      onChanged: onChanged,
      maxLines: obscureText ? 1 : maxLines,
      style: size == AppFieldSize.compact
          ? Theme.of(context).textTheme.bodyMedium
          : null,
      decoration: InputDecoration(
        labelText: label,
        hintText: hint,
        prefixIcon: prefixIcon,
        suffixIcon: suffixIcon,
        isDense: size == AppFieldSize.compact,
      ),
    );
  }
}
