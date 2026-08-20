import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_spacing.dart';

class AppForm extends StatelessWidget {
  const AppForm({
    super.key,
    required this.formKey,
    required this.children,
    this.spacing = AppSpacing.md,
  });

  final GlobalKey<FormState> formKey;
  final List<Widget> children;
  final double spacing;

  @override
  Widget build(BuildContext context) {
    return Form(
      key: formKey,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        mainAxisSize: MainAxisSize.min,
        children: [
          for (var i = 0; i < children.length; i++) ...[
            if (i > 0) SizedBox(height: spacing),
            children[i],
          ],
        ],
      ),
    );
  }
}
