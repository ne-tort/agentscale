import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/widgets/app_snack_bar.dart';
import 'package:prodavan/features/settings/settings_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Platform admin settings — credentials first, then global preferences.
class AdminSettingsBody extends StatefulWidget {
  const AdminSettingsBody({super.key});

  @override
  State<AdminSettingsBody> createState() => _AdminSettingsBodyState();
}

class _AdminSettingsBodyState extends State<AdminSettingsBody> {
  bool _loading = true;
  bool _passwordSet = false;
  String _username = '';

  @override
  void initState() {
    super.initState();
    _reload();
  }

  Future<void> _reload() async {
    try {
      final profile = await adminContext.api.getAdminProfile();
      if (!mounted) return;
      setState(() {
        _username = profile['username'] as String? ?? '';
        _passwordSet = profile['password_set'] == true;
        _loading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() => _loading = false);
    }
  }

  Future<void> _copyLogin() async {
    await Clipboard.setData(ClipboardData(text: _username));
    if (!mounted) return;
    AppSnackBar.info(context, AppLocalizations.of(context).companyIdCopied);
  }

  Future<void> _saveLogin(String raw) async {
    final trimmed = raw.trim();
    if (trimmed.length < 3) return;
    await adminContext.api.setAdminLogin(login: trimmed);
    if (!mounted) return;
    setState(() => _username = trimmed);
    AppSnackBar.success(
      context,
      AppLocalizations.of(context).companyPasswordChanged,
    );
  }

  Future<void> _savePassword(String raw) async {
    final trimmed = raw.trim();
    if (trimmed.length < 8) return;
    await adminContext.api.setAdminPassword(password: trimmed);
    if (!mounted) return;
    setState(() => _passwordSet = true);
    AppSnackBar.success(
      context,
      AppLocalizations.of(context).companyPasswordChanged,
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
          title: l10n.companyLogin,
          icon: Icons.badge_outlined,
          value: _username,
          hintText: l10n.companyLogin,
          validateInput: (v) => v.trim().length >= 3,
          onSave: _saveLogin,
          onTap: _copyLogin,
        ),
        AppValuePreference<String>(
          title: l10n.companyPassword,
          icon: Icons.key_outlined,
          value: '',
          obscureText: true,
          hintText: l10n.companyPasswordHint,
          invalidMessage: l10n.companyPasswordHint,
          presentValue: (_) => _passwordSet ? '••••••••' : l10n.commonNotSet,
          formatInputValue: (_) => '',
          validateInput: (raw) => raw.trim().length >= 8,
          onSave: _savePassword,
        ),
      ],
    );
  }
}
