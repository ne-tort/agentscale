import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';

/// Placeholder for system tabs without a dedicated interpreter yet (L05/L06).
class CabinetPlaceholderTabPage extends StatelessWidget {
  const CabinetPlaceholderTabPage({
    super.key,
    required this.title,
    required this.hint,
  });

  final String title;
  final String hint;

  @override
  Widget build(BuildContext context) {
    final colors = context.appColors;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(Icons.layers_outlined, size: 48, color: colors.border),
            const SizedBox(height: 16),
            Text(title, style: Theme.of(context).textTheme.titleLarge),
            const SizedBox(height: 8),
            Text(
              hint,
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                    color: colors.muted,
                  ),
            ),
          ],
        ),
      ),
    );
  }
}
