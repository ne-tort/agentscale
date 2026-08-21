import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';

/// Full-screen destructive confirm — replaces AlertDialog yes/no.
class DangerConfirmPage extends StatelessWidget {
  const DangerConfirmPage({
    super.key,
    required this.title,
    required this.message,
    this.confirmLabel = 'Удалить',
    this.cancelLabel = 'Отмена',
  });

  final String title;
  final String message;
  final String confirmLabel;
  final String cancelLabel;

  static Future<bool> push(
    BuildContext context, {
    required String title,
    required String message,
    String confirmLabel = 'Удалить',
  }) async {
    final result = await Navigator.of(context).push<bool>(
      MaterialPageRoute(
        builder: (_) => DangerConfirmPage(
          title: title,
          message: message,
          confirmLabel: confirmLabel,
        ),
      ),
    );
    return result ?? false;
  }

  @override
  Widget build(BuildContext context) {
    return AppScaffold(
      title: Text(title),
      body: Padding(
        padding: const EdgeInsets.all(AppSpacing.lg),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            InlineErrorBanner(message: message),
            const Spacer(),
            AppButton(
              label: cancelLabel,
              variant: AppButtonVariant.outlined,
              onPressed: () => Navigator.of(context).pop(false),
            ),
            const SizedBox(height: AppSpacing.sm),
            AppButton(
              label: confirmLabel,
              onPressed: () => Navigator.of(context).pop(true),
            ),
          ],
        ),
      ),
    );
  }
}
