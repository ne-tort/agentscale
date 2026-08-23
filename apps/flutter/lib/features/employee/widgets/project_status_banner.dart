import 'package:flutter/material.dart';

/// Subscription / project pause banners for chat workspace (L05).
class ProjectStatusBanner {
  ProjectStatusBanner._();

  static List<Widget> build(
    BuildContext context, {
    required bool companySuspended,
    required bool projectPaused,
    VoidCallback? onResume,
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
          content: const Text(
            'Project is paused — chat, uploads and agent runs are disabled',
          ),
          leading: const Icon(Icons.pause_circle_filled),
          backgroundColor: Theme.of(context).colorScheme.secondaryContainer,
          actions: [
            if (onResume != null)
              TextButton(
                onPressed: onResume,
                child: const Text('Resume'),
              )
            else
              const SizedBox.shrink(),
          ],
        ),
      ];
    }
    return const [];
  }
}
