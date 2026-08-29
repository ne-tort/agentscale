import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/widgets/app_snack_bar.dart';
import 'package:prodavan/features/employee/cabinet_picker_page.dart';
import 'package:prodavan/features/settings/settings_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Employee settings — password, contact email, company RO, switch cabinet.
class EmployeeSettingsBody extends StatefulWidget {
  const EmployeeSettingsBody({
    super.key,
    required this.cabinetId,
    required this.cabinetName,
  });

  final String cabinetId;
  final String cabinetName;

  @override
  State<EmployeeSettingsBody> createState() => _EmployeeSettingsBodyState();
}

class _EmployeeSettingsBodyState extends State<EmployeeSettingsBody> {
  bool _loading = true;
  String _login = '';
  String _contactEmail = '';
  String _companyName = '';

  @override
  void initState() {
    super.initState();
    _reload();
  }

  Future<void> _reload() async {
    try {
      final me = await workContext.api.me();
      if (!mounted) return;
      final employee = me['employee'] as Map<String, dynamic>?;
      final memberships = employee?['memberships'] as List?;
      String companyName = '';
      if (memberships is List && memberships.isNotEmpty) {
        companyName = memberships.first['company_name'] as String? ?? '';
      }
      setState(() {
        _login = employee?['email'] as String? ?? me['email'] as String? ?? '';
        _contactEmail = employee?['contact_email'] as String? ?? '';
        _companyName = companyName;
        _loading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() => _loading = false);
    }
  }

  Future<void> _savePassword(String raw) async {
    final trimmed = raw.trim();
    if (trimmed.length < 8) return;
    await workContext.api.setMyPassword(trimmed);
    if (!mounted) return;
    AppSnackBar.success(context, AppLocalizations.of(context).employeePasswordChanged);
  }

  Future<void> _saveContactEmail(String raw) async {
    await workContext.api.patchMyContactEmail(raw.trim());
    if (!mounted) return;
    setState(() => _contactEmail = raw.trim());
    AppSnackBar.success(context, AppLocalizations.of(context).employeeContactEmailSaved);
  }

  Future<void> _switchCabinet() async {
    final cabinets = await workContext.api.listCabinets();
    if (!mounted) return;
    if (cabinets.length <= 1) {
      AppSnackBar.info(context, AppLocalizations.of(context).employeeSingleCabinet);
      return;
    }
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(builder: (_) => const CabinetPickerPage()),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }
    return SettingsPage(
      embedded: true,
      leadingChildren: [
        AppValuePreference<String>(
          title: l10n.authLogin,
          icon: Icons.badge_outlined,
          value: _login,
          enabled: false,
          presentValue: (v) => v,
          onSave: (_) async {},
        ),
        AppValuePreference<String>(
          title: l10n.employeeContactEmail,
          icon: Icons.mail_outline,
          value: _contactEmail,
          onSave: _saveContactEmail,
        ),
        AppValuePreference<String>(
          title: l10n.employeePassword,
          icon: Icons.key_outlined,
          value: '',
          obscureText: true,
          hintText: l10n.companyPasswordHint,
          invalidMessage: l10n.companyPasswordHint,
          presentValue: (_) => '••••••••',
          formatInputValue: (_) => '',
          validateInput: (raw) => raw.trim().length >= 8,
          onSave: _savePassword,
        ),
        AppValuePreference<String>(
          title: l10n.commonCompany,
          icon: Icons.business_outlined,
          value: _companyName,
          enabled: false,
          onSave: (_) async {},
        ),
        AppValuePreference<String>(
          title: l10n.navCabinets,
          icon: Icons.view_module_outlined,
          value: widget.cabinetName,
          enabled: false,
          onSave: (_) async {},
        ),
        AppNavPreference(
          title: l10n.employeeSwitchCabinet,
          icon: Icons.swap_horiz_outlined,
          onTap: _switchCabinet,
        ),
      ],
    );
  }
}
