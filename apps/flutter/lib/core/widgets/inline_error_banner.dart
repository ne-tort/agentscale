import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_radii.dart';
import 'package:prodavan/core/theme/app_spacing.dart';

/// Field-level / inline hint only. API errors go through [AppSnackBar].
class InlineErrorBanner extends StatelessWidget {
  const InlineErrorBanner({super.key, required this.message});

  final String message;

  @override
  Widget build(BuildContext context) {
    final colors = context.appColors;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(AppSpacing.md),
      decoration: BoxDecoration(
        color: colors.dangerContainer,
        borderRadius: AppRadii.borderMd,
      ),
      child: Text(message, style: TextStyle(color: colors.onDangerContainer)),
    );
  }
}
