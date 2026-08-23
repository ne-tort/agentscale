import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_form.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_text_field.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';
import 'package:prodavan/features/employee/dynamic_cabinet_shell.dart';

/// Full-page cabinet create (L05 ux — no modals).
class CabinetCreatePage extends StatefulWidget {
  const CabinetCreatePage({super.key});

  @override
  State<CabinetCreatePage> createState() => _CabinetCreatePageState();
}

class _CabinetCreatePageState extends State<CabinetCreatePage> {
  final _formKey = GlobalKey<FormState>();
  final _nameCtrl = TextEditingController(text: 'My cabinet');
  bool _saving = false;
  String? _error;

  @override
  void dispose() {
    _nameCtrl.dispose();
    super.dispose();
  }

  Future<void> _create() async {
    if (!_formKey.currentState!.validate()) return;
    final companyId = workContext.companyId;
    if (companyId == null) {
      setState(() => _error = 'No company_id from /me memberships');
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
        _error = e.toString();
        _saving = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return AppScaffold(
      title: const Text('Create cabinet'),
      body: ListView(
        padding: const EdgeInsets.all(AppSpacing.lg),
        children: [
          if (_error != null) InlineErrorBanner(message: _error!),
          AppForm(
            formKey: _formKey,
            children: [
              AppTextField(
                controller: _nameCtrl,
                label: 'Cabinet name',
                enabled: !_saving,
                validator: (v) {
                  if (v == null || v.trim().isEmpty) return 'Name required';
                  return null;
                },
              ),
              AppButton(
                label: _saving ? 'Creating…' : 'Create cabinet',
                onPressed: _saving ? null : _create,
              ),
            ],
          ),
        ],
      ),
    );
  }
}
