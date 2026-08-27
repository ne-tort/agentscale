import 'package:flutter/material.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';

import 'package:prodavan/core/preferences/preferences.dart';
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
  String _name = '';
  bool _saving = false;
  Object? _error;
  bool _seeded = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (!_seeded) {
      _seeded = true;
      _name = AppLocalizations.of(context).cabinetMyCabinetDefault;
    }
  }

  Future<void> _create() async {
    final l10n = AppLocalizations.of(context);
    final name = _name.trim();
    if (name.isEmpty) {
      setState(() => _error = l10n.commonNameRequired);
      return;
    }
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
        name: name,
        companyId: companyId,
      );
      if (!mounted) return;
      final cabinetId = cabinet['id'] as String;
      final cabinetName = cabinet['name'] as String? ?? name;
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
          AppValuePreference<String>(
            title: l10n.cabinetCabinetName,
            icon: Icons.meeting_room_outlined,
            value: _name,
            enabled: !_saving,
            presentValue: (v) => v.isEmpty ? l10n.commonNotSet : v,
            onSave: (v) async => setState(() => _name = v.trim()),
          ),
          Padding(
            padding: const EdgeInsets.all(AppSpacing.md),
            child: AppAsyncButton(
              label: _saving ? l10n.commonCreating : l10n.cabinetCreateCabinet,
              busy: _saving,
              onPressed: _saving ? null : _create,
            ),
          ),
        ],
      ),
    );
  }
}
