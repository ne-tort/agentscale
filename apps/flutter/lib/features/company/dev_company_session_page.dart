import 'package:flutter/material.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';

import 'package:prodavan/core/config/api_base.dart';
import 'package:prodavan/core/session/company_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_catalog_select_page.dart';
import 'package:prodavan/features/company/company_shell.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Dev-only Company admin bearer entry (L04).
class DevCompanySessionPage extends StatefulWidget {
  const DevCompanySessionPage({super.key});

  @override
  State<DevCompanySessionPage> createState() => _DevCompanySessionPageState();
}

class _DevCompanySessionPageState extends State<DevCompanySessionPage> {
  final _baseCtrl = TextEditingController(text: ApiBase.value);
  final _tokenCtrl = TextEditingController();
  Object? _error;

  @override
  void dispose() {
    _baseCtrl.dispose();
    _tokenCtrl.dispose();
    super.dispose();
  }

  Future<void> _connect() async {
    setState(() => _error = null);
    companyContext.setSession(
      baseUrl: _baseCtrl.text.trim(),
      bearerToken: _tokenCtrl.text.trim(),
    );
    try {
      final l10n = AppLocalizations.of(context);
      final me = await companyContext.api.me();
      final contours = me['contours'];
      if (contours is! List || !contours.contains('company')) {
        companyContext.clear();
        setState(() => _error = l10n.devTokenMustHaveCompanyContour);
        return;
      }
      final employee = me['employee'] as Map<String, dynamic>?;
      final memberships = employee?['memberships'];
      if (memberships is! List) {
        companyContext.clear();
        setState(() => _error = l10n.devNoEmployeeMemberships);
        return;
      }
      final adminMemberships = memberships
          .whereType<Map<String, dynamic>>()
          .where((m) => m['role'] == 'company.admin')
          .toList();
      if (adminMemberships.isEmpty) {
        companyContext.clear();
        setState(() => _error = l10n.devCompanyAdminMembershipRequired);
        return;
      }
      if (!mounted) return;
      if (adminMemberships.length == 1) {
        final id = adminMemberships.first['company_id'] as String;
        companyContext.selectCompany(id: id, name: id);
        _openShell();
        return;
      }
      final picked = await Navigator.of(context).push<Set<String>>(
        MaterialPageRoute(
          builder: (_) => AppCatalogSelectPage(
            title: l10n.commonSelectCompany,
            items: [
              for (final m in adminMemberships)
                AppCatalogSelectItem(
                  id: m['company_id'] as String,
                  title: m['company_id'] as String,
                  subtitle: m['role'] as String?,
                ),
            ],
            popOnSelect: true,
          ),
        ),
      );
      if (picked == null || picked.isEmpty) {
        companyContext.clear();
        return;
      }
      final id = picked.first;
      companyContext.selectCompany(id: id, name: id);
      if (!mounted) return;
      _openShell();
    } catch (e) {
      companyContext.clear();
      setState(() => _error = e);
    }
  }

  void _openShell() {
    Navigator.of(context).pushReplacement(
      MaterialPageRoute<void>(builder: (_) => const CompanyShell()),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return AppScaffold(
      title: Text(l10n.devCompanySession),
      body: Padding(
        padding: EdgeInsets.all(AppSpacing.lg),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            TextField(
              controller: _baseCtrl,
              decoration: InputDecoration(labelText: l10n.commonApiBaseUrl),
            ),
            const SizedBox(height: AppSpacing.md),
            TextField(
              controller: _tokenCtrl,
              decoration: InputDecoration(labelText: l10n.devBearerTokenCompanyAdmin),
            ),
            if (_error != null) ...[
              const SizedBox(height: AppSpacing.sm),
              Text(AppErrors.localize(context, _error!), style: TextStyle(color: context.appColors.danger)),
            ],
            const SizedBox(height: AppSpacing.lg),
            AppButton(label: l10n.commonContinueAction, onPressed: _connect),
          ],
        ),
      ),
    );
  }
}
