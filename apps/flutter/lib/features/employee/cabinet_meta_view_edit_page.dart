import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Edit collection view ui_json subset (L06 PATCH views).
class CabinetMetaViewEditPage extends StatefulWidget {
  const CabinetMetaViewEditPage({
    super.key,
    required this.cabinetId,
    required this.viewSlug,
    required this.uiJson,
  });

  final String cabinetId;
  final String viewSlug;
  final Map<String, dynamic> uiJson;

  @override
  State<CabinetMetaViewEditPage> createState() => _CabinetMetaViewEditPageState();
}

class _CabinetMetaViewEditPageState extends State<CabinetMetaViewEditPage> {
  final _formKey = GlobalKey<FormState>();
  late final TextEditingController _titleField;
  bool _saving = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _titleField = TextEditingController(text: '${widget.uiJson['title_field'] ?? 'title'}');
  }

  @override
  void dispose() {
    _titleField.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    if (!_formKey.currentState!.validate()) return;
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      final ui = Map<String, dynamic>.from(widget.uiJson);
      ui['title_field'] = _titleField.text.trim();
      await workContext.api.updateMetaView(
        cabinetId: widget.cabinetId,
        viewSlug: widget.viewSlug,
        uiJson: ui,
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
    final l10n = AppLocalizations.of(context);
    return AppScaffold(
      title: Text(l10n.cabinetViewTitle(widget.viewSlug)),
      body: ListView(
        padding: const EdgeInsets.all(AppSpacing.lg),
        children: [
          if (_error != null) InlineErrorBanner(message: _error!),
          Form(
            key: _formKey,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                TextFormField(
                  controller: _titleField,
                  decoration: InputDecoration(labelText: l10n.cabinetTitleFieldColumnName),
                  enabled: !_saving,
                  validator: (v) => (v ?? '').trim().isEmpty ? l10n.commonRequired : null,
                ),
                const SizedBox(height: AppSpacing.md),
                AppButton(
                  label: _saving ? l10n.commonSaving : l10n.cabinetSaveView,
                  onPressed: _saving ? null : _save,
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
