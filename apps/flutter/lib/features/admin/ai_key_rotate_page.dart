import 'package:flutter/material.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';

import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_password_field.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Full-page secret rotation — no modal (L04 ux-contract).
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
  final _formKey = GlobalKey<FormState>();
  final _secretCtrl = TextEditingController();
  bool _saving = false;
  Object? _error;

  @override
  void dispose() {
    _secretCtrl.dispose();
    super.dispose();
  }

  Future<void> _rotate() async {
    if (!_formKey.currentState!.validate()) return;
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await adminContext.api.rotateAiKeySecret(
        keyId: widget.keyId,
        secret: _secretCtrl.text,
      );
      if (!mounted) return;
      Navigator.of(context).pop(true);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e;
        _saving = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return AppScaffold(
      title: Text(l10n.adminRotateKeyTitle(widget.keyName)),
      body: Padding(
        padding: EdgeInsets.all(AppSpacing.lg),
        child: Form(
          key: _formKey,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              if (_error != null)
                AppStatusBanner(
                  severity: AppStatusSeverity.error,
                  message: AppErrors.localize(context, _error!),
                ),
              Text(l10n.adminRotateSecretHint),
              SizedBox(height: AppSpacing.md),
              AppPasswordField(
                controller: _secretCtrl,
                label: l10n.adminNewSecret,
                enabled: !_saving,
                validator: (v) {
                  if (v == null || v.isEmpty) return l10n.adminSecretRequired;
                  return null;
                },
              ),
              SizedBox(height: AppSpacing.md),
              AppAsyncButton(
                label: _saving ? l10n.adminRotating : l10n.adminRotateSecret,
                busy: _saving,
                onPressed: _saving ? null : _rotate,
              ),
            ],
          ),
        ),
      ),
    );
  }
}
