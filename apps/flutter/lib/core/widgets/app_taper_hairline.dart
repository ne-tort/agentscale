import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';

/// Horizontal rule that fades toward both ends (rail section separator).
class AppTaperHairline extends StatelessWidget {
  const AppTaperHairline({
    super.key,
    this.height = 12,
    this.thickness = 1,
    this.horizontalInset = AppSpacing.md,
  });

  /// Total vertical slot (line is centered).
  final double height;

  /// Peak stroke thickness at the center.
  final double thickness;

  /// Soft inset from the rail edges before the fade begins.
  final double horizontalInset;

  @override
  Widget build(BuildContext context) {
    final colors = context.appColors;
    final peak = colors.border.withValues(alpha: 0.85);
    final edge = colors.border.withValues(alpha: 0);

    return SizedBox(
      height: height,
      width: double.infinity,
      child: Padding(
        padding: EdgeInsets.symmetric(horizontal: horizontalInset),
        child: Center(
          child: SizedBox(
            height: thickness,
            width: double.infinity,
            child: DecoratedBox(
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  colors: [edge, peak, peak, edge],
                  stops: const [0.0, 0.28, 0.72, 1.0],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
