import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_confirm_page.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_snack_bar.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/features/company/company_employee_cabinets_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Employee metadata — login, password, cabinets selector, pause/resume.
class CompanyEmployeeDetailPage extends StatefulWidget {
  const CompanyEmployeeDetailPage({
    super.key,
    required this.companyId,
    required this.employeeId,
    required this.employeeLogin,
    this.contactEmail,
    this.status = 'active',
  });

  final String companyId;
  final String employeeId;
  final String employeeLogin;
  final String? contactEmail;
  final String status;

  @override
  State<CompanyEmployeeDetailPage> createState() =>
      _CompanyEmployeeDetailPageState();
}

class _CompanyEmployeeDetailPageState extends State<CompanyEmployeeDetailPage> {
  late String _contactEmail;
  late String _status;
  bool _passwordSet = true;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _contactEmail = widget.contactEmail ?? '';
    _status = widget.status;
  }

  bool get _isDisabled => _status == 'disabled';

  Future<void> _copyLogin() async {
    await Clipboard.setData(ClipboardData(text: widget.employeeLogin));
    if (!mounted) return;
    AppSnackBar.info(context, AppLocalizations.of(context).companyIdCopied);
  }

  Future<void> _savePassword(String raw) async {
    final trimmed = raw.trim();
    if (trimmed.length < 8) return;
    await companyContext.api.setEmployeePassword(
      companyId: widget.companyId,
      employeeId: widget.employeeId,
      password: trimmed,
    );
    if (!mounted) return;
    setState(() => _passwordSet = true);
    AppSnackBar.success(
      context,
      AppLocalizations.of(context).companyPasswordChanged,
    );
  }

  Future<void> _saveContactEmail(String raw) async {
    final trimmed = raw.trim();
    await companyContext.api.updateEmployeeContactEmail(
      companyId: widget.companyId,
      employeeId: widget.employeeId,
      contactEmail: trimmed.isEmpty ? null : trimmed,
    );
    if (!mounted) return;
    setState(() => _contactEmail = trimmed);
  }

  Future<void> _toggleStatus() async {
    final l10n = AppLocalizations.of(context);
    if (_isDisabled) {
      final ok = await AppConfirmPage.push(
        context,
        title: l10n.companyEnableEmployee,
        message: l10n.companyEnableEmployeeConfirm(widget.employeeLogin),
        confirmLabel: l10n.companyEnableEmployee,
      );
      if (!ok || !mounted) return;
      setState(() => _busy = true);
      try {
        final body = await companyContext.api.enableEmployee(widget.employeeId);
        if (!mounted) return;
        setState(() {
          _status = body['status'] as String? ?? 'active';
          _busy = false;
        });
      } catch (e) {
        if (mounted) {
          setState(() => _busy = false);
          AppErrors.showSnack(context, e);
        }
      }
      return;
    }

    final ok = await AppConfirmPage.push(
      context,
      title: l10n.companyPauseEmployee,
      message: l10n.companyPauseEmployeeConfirm(widget.employeeLogin),
      confirmLabel: l10n.companyPauseEmployee,
      severity: AppStatusSeverity.warning,
    );
    if (!ok || !mounted) return;
    setState(() => _busy = true);
    try {
      final body = await companyContext.api.disableEmployee(widget.employeeId);
      if (!mounted) return;
      setState(() {
        _status = body['status'] as String? ?? 'disabled';
        _busy = false;
      });
    } catch (e) {
      if (mounted) {
        setState(() => _busy = false);
        AppErrors.showSnack(context, e);
      }
    }
  }

  Future<void> _openCabinets() async {
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => CompanyEmployeeCabinetsPage(
          companyId: widget.companyId,
          employeeId: widget.employeeId,
          employeeEmail: widget.employeeLogin,
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return AppScaffold(
      title: Text(widget.employeeLogin),
      body: ListView(
        padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
        children: [
          AppValuePreference<String>(
            title: l10n.companyLogin,
            icon: Icons.badge_outlined,
            value: widget.employeeLogin,
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
            presentValue: (_) =>
                _passwordSet ? '••••••••' : l10n.commonNotSet,
            formatInputValue: (_) => '',
            validateInput: (raw) => raw.trim().length >= 8,
            onSave: _savePassword,
          ),
          AppValuePreference<String>(
            title: l10n.commonEmail,
            icon: Icons.email_outlined,
            value: _contactEmail,
            hintText: l10n.commonEmail,
            keyboardType: TextInputType.emailAddress,
            presentValue: (v) =>
                v.trim().isEmpty ? l10n.commonNotSet : v,
            onSave: _saveContactEmail,
          ),
          AppNavPreference(
            title: l10n.commonCabinets,
            icon: Icons.view_module_outlined,
            onTap: _openCabinets,
          ),
          AppNavPreference(
            title: _isDisabled ? l10n.companyEnableEmployee : l10n.companyPauseEmployee,
            icon: _isDisabled ? Icons.play_circle_outline : Icons.pause_circle_outline,
            enabled: !_busy,
            onTap: _toggleStatus,
          ),
        ],
      ),
    );
  }
}
