import 'package:flutter/material.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';

import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Full-page secret rotation — same chrome as AI key detail secret field.
class AdminAiKeyRotatePage extends StatefulWidget {
  const AdminAiKeyRotatePage({
    super.key,
    required this.keyId,
    required this.keyName,
  });

  final String keyId;
  final String keyName;

  @override
  State<AdminAiKeyRotatePage> createState() => _AdminAiKeyRotatePageState();
}

class _AdminAiKeyRotatePageState extends State<AdminAiKeyRotatePage> {
  Object? _error;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return AppScaffold(
      title: Text(l10n.adminRotateKeyTitle(widget.keyName)),
      body: ListView(
        padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
        children: [
          if (_error != null)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md),
              child: AppStatusBanner(
                severity: AppStatusSeverity.error,
                message: AppErrors.localize(context, _error!),
              ),
            ),
          Padding(
            padding: const EdgeInsets.symmetric(
              horizontal: AppSpacing.md,
              vertical: AppSpacing.sm,
            ),
            child: Text(l10n.adminRotateSecretHint),
          ),
          AppValuePreference<String>(
            title: l10n.adminNewSecret,
            icon: Icons.key_outlined,
            value: '',
            obscureText: true,
            presentValue: (_) => l10n.commonNotSet,
            formatInputValue: (_) => '',
            invalidMessage: l10n.adminSecretRequired,
            validateInput: (raw) => raw.trim().isNotEmpty,
            onSave: (v) async {
              final secret = v.trim();
              if (secret.isEmpty) return;
              try {
                await adminContext.api.rotateAiKeySecret(
                  keyId: widget.keyId,
                  secret: secret,
                );
                if (!context.mounted) return;
                Navigator.of(context).pop(true);
              } catch (e) {
                if (!context.mounted) return;
                setState(() => _error = e);
              }
            },
          ),
        ],
      ),
    );
  }
}
