import 'package:flutter/material.dart';

import 'package:prodavan/core/widgets/empty_placeholder.dart';

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
    return EmptyPlaceholder(
      icon: Icons.layers_outlined,
      title: title,
      subtitle: hint,
    );
  }
}
