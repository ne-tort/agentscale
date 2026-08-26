import 'package:flutter/material.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/features/employee/dynamic_cabinet_shell.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Full-page cabinet create (L05 ux — no modals).
class CabinetCreatePage extends StatefulWidget {
  const CabinetCreatePage({super.key});

  @override
  State<CabinetCreatePage> createState() => _CabinetCreatePageState();
}

class _CabinetCreatePageState extends State<CabinetCreatePage> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      if (_nameCtrl.text.isEmpty) {
        _nameCtrl.text = AppLocalizations.of(context).cabinetMyCabinetDefault;
      }
    });
  }

  final _formKey = GlobalKey<FormState>();
  final _nameCtrl = TextEditingController();
  bool _saving = false;
  Object? _error;

  @override
  void dispose() {
    _nameCtrl.dispose();
    super.dispose();
  }

  Future<void> _create() async {
    if (!_formKey.currentState!.validate()) return;
    final l10n = AppLocalizations.of(context);
    final companyId = workContext.companyId;
    if (companyId == null) {
      setState(() => _error = l10n.cabinetNoCompanyIdFromMe);
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      final cabinet = await workContext.api.createCabinet(
        name: _nameCtrl.text.trim(),
        companyId: companyId,
      );
      if (!mounted) return;
      final cabinetId = cabinet['id'] as String;
      final cabinetName = cabinet['name'] as String? ?? _nameCtrl.text.trim();
      workContext.enterCabinet(cabinetId);
      Navigator.of(context).pushReplacement(
        MaterialPageRoute<void>(
          builder: (_) => DynamicCabinetShell(cabinetId: cabinetId, cabinetName: cabinetName),
        ),
      );
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
      title: Text(l10n.cabinetCreateCabinet),
      body: ListView(
        padding: const EdgeInsets.all(AppSpacing.lg),
        children: [
          if (_error != null) AppStatusBanner(severity: AppStatusSeverity.error, message: AppErrors.localize(context, _error!)),
          Form(
            key: _formKey,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                TextFormField(
                  controller: _nameCtrl,
                  decoration: InputDecoration(labelText: l10n.cabinetCabinetName),
                  enabled: !_saving,
                  validator: (v) {
                    if (v == null || v.trim().isEmpty) return l10n.commonNameRequired;
                    return null;
                  },
                ),
                const SizedBox(height: AppSpacing.md),
                AppButton(
                  label: _saving ? l10n.commonCreating : l10n.cabinetCreateCabinet,
                  onPressed: _saving ? null : _create,
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
