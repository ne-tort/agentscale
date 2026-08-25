import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_section_header.dart';

/// Group of preference controls under an optional section header.
class AppPreferenceSection extends StatelessWidget {
  const AppPreferenceSection({
    super.key,
    required this.title,
    required this.children,
    this.subtitle,
  });

  final String title;
  final String? subtitle;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        AppSectionHeader(title: title, subtitle: subtitle),
        ...children,
        const SizedBox(height: AppSpacing.lg),
      ],
    );
  }
}
