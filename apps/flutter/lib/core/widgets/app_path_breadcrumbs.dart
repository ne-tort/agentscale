import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_spacing.dart';

/// Horizontal path breadcrumbs for file/path browsers.
class AppPathBreadcrumbs extends StatelessWidget {
  const AppPathBreadcrumbs({
    super.key,
    required this.path,
    required this.onNavigate,
    this.rootLabel = '/',
  });

  /// Current path without leading slash (empty = root).
  final String path;
  final ValueChanged<String> onNavigate;
  final String rootLabel;

  List<String> get _segments {
    if (path.isEmpty) return const [];
    return path.split('/');
  }

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.md,
        vertical: AppSpacing.sm,
      ),
      child: Row(
        children: [
          _PathChip(
            label: rootLabel,
            selected: path.isEmpty,
            onTap: () => onNavigate(''),
          ),
          for (var i = 0; i < _segments.length; i++) ...[
            const Icon(Icons.chevron_right, size: 18),
            _PathChip(
              label: _segments[i],
              selected: i == _segments.length - 1,
              onTap: () => onNavigate(_segments.sublist(0, i + 1).join('/')),
            ),
          ],
        ],
      ),
    );
  }
}

class _PathChip extends StatelessWidget {
  const _PathChip({
    required this.label,
    required this.selected,
    required this.onTap,
  });

  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return ActionChip(
      label: Text(label),
      onPressed: onTap,
      backgroundColor: selected ? theme.colorScheme.primaryContainer : null,
    );
  }
}
