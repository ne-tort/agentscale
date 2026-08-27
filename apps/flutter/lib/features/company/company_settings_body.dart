import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/widgets/app_snack_bar.dart';
import 'package:prodavan/features/settings/settings_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Company settings — credentials first, then global preferences.
class CompanySettingsBody extends StatefulWidget {
  const CompanySettingsBody({super.key, required this.companyId});

  final String companyId;

  @override
  State<CompanySettingsBody> createState() => _CompanySettingsBodyState();
}

class _CompanySettingsBodyState extends State<CompanySettingsBody> {
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
      final summary = await companyContext.api.getSummary(widget.companyId);
      if (!mounted) return;
      setState(() {
        _username = summary['username'] as String? ?? widget.companyId;
        _passwordSet = summary['password_set'] == true;
        _loading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _username = widget.companyId;
        _loading = false;
      });
    }
  }

  Future<void> _copyLogin() async {
    await Clipboard.setData(ClipboardData(text: _username));
    if (!mounted) return;
    AppSnackBar.info(context, AppLocalizations.of(context).companyIdCopied);
  }

  Future<void> _savePassword(String raw) async {
    final trimmed = raw.trim();
    if (trimmed.length < 8) return;
    await companyContext.api.setCompanyPassword(
      companyId: widget.companyId,
      password: trimmed,
    );
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
          title: l10n.companyLoginId,
          icon: Icons.badge_outlined,
          value: _username,
          enabled: false,
          presentValue: (v) => v,
          onSave: (_) async {},
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
