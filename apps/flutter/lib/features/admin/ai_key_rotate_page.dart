import 'package:flutter/material.dart';

import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_form.dart';
import 'package:prodavan/core/widgets/app_password_field.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';

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
  String? _error;

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
        _error = e.toString();
        _saving = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return AppScaffold(
      title: Text('Rotate ${widget.keyName}'),
      body: Padding(
        padding: const EdgeInsets.all(AppSpacing.lg),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (_error != null) InlineErrorBanner(message: _error!),
            const Text('New secret replaces the stored value. Old secret is deleted from the file store.'),
            const SizedBox(height: AppSpacing.md),
            AppForm(
              formKey: _formKey,
              children: [
                AppPasswordField(
                  controller: _secretCtrl,
                  label: 'New secret',
                  validator: (v) {
                    if (v == null || v.isEmpty) return 'Secret required';
                    return null;
                  },
                ),
                AppButton(
                  label: _saving ? 'Rotating…' : 'Rotate secret',
                  onPressed: _saving ? null : _rotate,
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
