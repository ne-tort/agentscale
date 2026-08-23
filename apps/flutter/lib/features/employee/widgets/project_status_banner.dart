import 'package:flutter/material.dart';

/// Subscription / project pause banners for chat workspace (L05).
class ProjectStatusBanner {
  ProjectStatusBanner._();

  static List<Widget> build(
    BuildContext context, {
    required bool companySuspended,
    required bool projectPaused,
  }) {
    if (companySuspended) {
      return [
        MaterialBanner(
          content: const Text('Company subscription expired — chat and uploads are disabled'),
          leading: const Icon(Icons.pause_circle_outline),
          backgroundColor: Theme.of(context).colorScheme.errorContainer,
          actions: const [SizedBox.shrink()],
        ),
      ];
    }
    if (projectPaused) {
      return [
        MaterialBanner(
          content: const Text('Project is paused — chat and uploads are disabled'),
          leading: const Icon(Icons.pause_circle_filled),
          backgroundColor: Theme.of(context).colorScheme.secondaryContainer,
          actions: const [SizedBox.shrink()],
        ),
      ];
    }
    return const [];
  }
}
