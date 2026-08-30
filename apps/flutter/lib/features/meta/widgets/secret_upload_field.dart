import 'package:flutter/material.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Masked secret upload for secret_ref columns — stores ref only, never plaintext in row.
class SecretUploadField extends StatefulWidget {
  const SecretUploadField({
    super.key,
    required this.label,
    required this.value,
    required this.cabinetId,
    required this.moduleId,
    required this.api,
    required this.onChanged,
    this.readOnly = false,
  });

  final String label;
  final dynamic value;
  final String cabinetId;
  final String moduleId;
  final ProdavanApi api;
  final ValueChanged<Map<String, dynamic>?> onChanged;
  final bool readOnly;

  @override
  State<SecretUploadField> createState() => _SecretUploadFieldState();
}

class _SecretUploadFieldState extends State<SecretUploadField> {
  final _controller = TextEditingController();
  bool _uploading = false;

  Map<String, dynamic>? get _ref =>
      widget.value is Map ? Map<String, dynamic>.from(widget.value as Map) : null;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _upload() async {
    final secret = _controller.text.trim();
    if (secret.isEmpty) return;
    setState(() => _uploading = true);
    try {
      final ref = await widget.api.uploadCabinetModuleSecret(
        cabinetId: widget.cabinetId,
        moduleId: widget.moduleId,
        secret: secret,
        label: widget.label,
      );
      _controller.clear();
      widget.onChanged(ref);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('$e')),
        );
      }
    } finally {
      if (mounted) setState(() => _uploading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final ref = _ref;
    final prefix = ref?['secret_ref_prefix'] as String? ??
        ref?['secret_ref'] as String? ??
        '—';

    return Padding(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.md,
        vertical: AppSpacing.sm,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(widget.label, style: Theme.of(context).textTheme.titleSmall),
          const SizedBox(height: AppSpacing.xs),
          if (ref != null)
            Text(
              l10n.metaSecretConfigured(prefix),
              style: Theme.of(context).textTheme.bodyMedium,
            ),
          if (!widget.readOnly) ...[
            const SizedBox(height: AppSpacing.sm),
            TextField(
              controller: _controller,
              obscureText: true,
              enableSuggestions: false,
              autocorrect: false,
              decoration: InputDecoration(
                labelText: ref == null ? l10n.metaSecretEnter : l10n.metaSecretReplace,
                border: const OutlineInputBorder(),
              ),
              onSubmitted: (_) => _upload(),
            ),
            const SizedBox(height: AppSpacing.sm),
            Row(
              children: [
                FilledButton(
                  onPressed: _uploading || _controller.text.trim().isEmpty ? null : _upload,
                  child: _uploading
                      ? const SizedBox(
                          width: 18,
                          height: 18,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : Text(l10n.metaSecretSave),
                ),
                if (ref != null) ...[
                  const SizedBox(width: AppSpacing.sm),
                  TextButton(
                    onPressed: _uploading ? null : () => widget.onChanged(null),
                    child: Text(l10n.commonRemove),
                  ),
                ],
              ],
            ),
          ],
        ],
      ),
    );
  }
}
