import 'package:flutter/material.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Subscription / project pause banners for chat workspace (L05).
class ProjectStatusBanner {
  ProjectStatusBanner._();

  static List<Widget> build(
    BuildContext context, {
    required bool companySuspended,
    required bool projectPaused,
    VoidCallback? onResume,
  }) {
    final l10n = AppLocalizations.of(context);
    if (companySuspended) {
      return [
        MaterialBanner(
          content: Text(l10n.projectSubscriptionExpiredBanner),
          leading: const Icon(Icons.pause_circle_outline),
          backgroundColor: context.appColors.dangerContainer,
          actions: [SizedBox.shrink()],
        ),
      ];
    }
    if (projectPaused) {
      return [
        MaterialBanner(
          content: Text(l10n.projectPausedBanner),
          leading: Icon(Icons.pause_circle_filled),
          backgroundColor: context.appColors.surfaceContainer,
          actions: [
            if (onResume != null)
              TextButton(
                onPressed: onResume,
                child: Text(l10n.commonResume),
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
