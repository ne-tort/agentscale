import 'package:flutter/material.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
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
      AppErrors.showSnack(context, l10n.commonNameRequired);
      return;
    }
    final companyId = workContext.companyId;
    if (companyId == null) {
      AppErrors.showSnack(context, l10n.cabinetNoCompanyIdFromMe);
      return;
    }
    setState(() => _saving = true);
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
      setState(() => _saving = false);
      AppErrors.showSnack(context, e);
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
          AppValuePreference<String>(
            title: l10n.cabinetCabinetName,
            icon: Icons.meeting_room_outlined,
            value: _name,
            enabled: !_saving,
            presentValue: (v) => v.isEmpty ? l10n.commonNotSet : v,
            onSave: (v) async => setState(() => _name = v.trim()),
          ),
          if (_saving)
            const Padding(
              padding: EdgeInsets.all(AppSpacing.lg),
              child: Center(child: CircularProgressIndicator(strokeWidth: 2)),
            )
          else
            AppNavPreference(
              title: l10n.commonCreate,
              icon: Icons.add_rounded,
              onTap: _create,
            ),
        ],
      ),
    );
  }
}
