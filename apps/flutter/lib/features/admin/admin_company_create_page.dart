import 'package:flutter/material.dart';

import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_form.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_section_header.dart';
import 'package:prodavan/core/widgets/app_text_field.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Full-page company create + optional cabinet quotas (L04 ux-contract — no modals).
class AdminCompanyCreatePage extends StatefulWidget {
  const AdminCompanyCreatePage({super.key});

  @override
  State<AdminCompanyCreatePage> createState() => _AdminCompanyCreatePageState();
}

class _AdminCompanyCreatePageState extends State<AdminCompanyCreatePage> {
  final _formKey = GlobalKey<FormState>();
  final _nameCtrl = TextEditingController();
  final _emailCtrl = TextEditingController();
  final _displayNameCtrl = TextEditingController();
  final _maxCabinetsCtrl = TextEditingController(text: '10');
  final _maxPackagesCtrl = TextEditingController(text: '20');
  final _maxBundleMbCtrl = TextEditingController(text: '50');
  final _subscriptionEndsCtrl = TextEditingController();

  bool _saving = false;
  bool _subscriptionLifetime = false;
  String? _error;

  static const _defaultMaxCabinets = 10;
  static const _defaultMaxPackages = 20;
  static const _defaultMaxBundleMb = 50;

  @override
  void dispose() {
    _nameCtrl.dispose();
    _emailCtrl.dispose();
    _displayNameCtrl.dispose();
    _maxCabinetsCtrl.dispose();
    _maxPackagesCtrl.dispose();
    _maxBundleMbCtrl.dispose();
    _subscriptionEndsCtrl.dispose();
    super.dispose();
  }

  int? _parsePositive(String raw) {
    final v = int.tryParse(raw.trim());
    if (v == null || v < 1) return null;
    return v;
  }

  bool get _quotasChanged {
    final maxC = _parsePositive(_maxCabinetsCtrl.text);
    final maxP = _parsePositive(_maxPackagesCtrl.text);
    final maxB = _parsePositive(_maxBundleMbCtrl.text);
    if (maxC == null || maxP == null || maxB == null) return false;
    return maxC != _defaultMaxCabinets ||
        maxP != _defaultMaxPackages ||
        maxB != _defaultMaxBundleMb;
  }

  Future<void> _create() async {
    if (!_formKey.currentState!.validate()) return;
    final l10n = AppLocalizations.of(context);
    final maxCabinets = _parsePositive(_maxCabinetsCtrl.text);
    final maxPackages = _parsePositive(_maxPackagesCtrl.text);
    final maxBundleMb = _parsePositive(_maxBundleMbCtrl.text);
    if (maxCabinets == null || maxPackages == null || maxBundleMb == null) {
      setState(() => _error = l10n.adminCabinetQuotasMustBePositive);
      return;
    }

    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      final displayName = _displayNameCtrl.text.trim();
      final body = await adminContext.api.createCompany(
        name: _nameCtrl.text.trim(),
        adminEmail: _emailCtrl.text.trim(),
        adminDisplayName: displayName.isEmpty ? null : displayName,
      );
      final company = body['company'] as Map<String, dynamic>? ?? body;
      final companyId = company['id'] as String;
      final companyName = company['name'] as String? ?? _nameCtrl.text.trim();

      if (_quotasChanged) {
        await adminContext.api.setCabinetQuotas(
          companyId: companyId,
          maxCabinets: maxCabinets,
          maxPackagesPerCabinet: maxPackages,
          maxBundleImportMb: maxBundleMb,
        );
      }

      if (_subscriptionLifetime || _subscriptionEndsCtrl.text.trim().isNotEmpty) {
        final endsRaw = _subscriptionEndsCtrl.text.trim();
        await adminContext.api.setCompanySubscription(
          companyId: companyId,
          subscriptionLifetime: _subscriptionLifetime,
          subscriptionEndsAt: _subscriptionLifetime
              ? null
              : (endsRaw.contains('T') ? endsRaw : '${endsRaw}T00:00:00Z'),
        );
      }

      if (!mounted) return;
      Navigator.of(context).pop(<String, String>{
        'id': companyId,
        'name': companyName,
      });
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
      title: Text(l10n.adminCreateCompany),
      body: ListView(
        padding: EdgeInsets.all(AppSpacing.lg),
        children: [
          if (_error != null) InlineErrorBanner(message: _error!),
          Text(l10n.adminInviteCompanyAdminViaKeycloak),
          const SizedBox(height: AppSpacing.md),
          AppForm(
            formKey: _formKey,
            children: [
              AppSectionHeader(title: l10n.commonCompany),
              AppTextField(
                controller: _nameCtrl,
                label: l10n.adminCompanyName,
                validator: (v) {
                  if (v == null || v.trim().isEmpty) return l10n.commonNameRequired;
                  return null;
                },
              ),
              AppSectionHeader(title: l10n.adminCompanyAdminInvite),
              AppTextField(
                controller: _emailCtrl,
                label: l10n.adminAdminEmail,
                keyboardType: TextInputType.emailAddress,
                validator: (v) {
                  final email = v?.trim() ?? '';
                  if (email.isEmpty || !email.contains('@')) return l10n.companyValidEmailRequired;
                  return null;
                },
              ),
              AppTextField(
                controller: _displayNameCtrl,
                label: l10n.commonDisplayNameOptional,
              ),
              AppSectionHeader(title: l10n.adminCabinetQuotas),
              AppTextField(
                controller: _maxCabinetsCtrl,
                label: l10n.adminMaxCabinets,
                keyboardType: TextInputType.number,
                validator: (v) => _parsePositive(v ?? '') == null ? l10n.commonPositiveInteger : null,
              ),
              AppTextField(
                controller: _maxPackagesCtrl,
                label: l10n.adminMaxPackagesPerCabinet,
                keyboardType: TextInputType.number,
                validator: (v) => _parsePositive(v ?? '') == null ? l10n.commonPositiveInteger : null,
              ),
              AppTextField(
                controller: _maxBundleMbCtrl,
                label: l10n.adminMaxBundleImportMb,
                keyboardType: TextInputType.number,
                validator: (v) => _parsePositive(v ?? '') == null ? l10n.commonPositiveInteger : null,
              ),
              AppSectionHeader(title: l10n.adminProdavanSubscriptionOptional),
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                title: Text(l10n.adminLifetimeSubscription),
                value: _subscriptionLifetime,
                onChanged: _saving ? null : (v) => setState(() => _subscriptionLifetime = v),
              ),
              AppTextField(
                controller: _subscriptionEndsCtrl,
                label: l10n.adminEndsAtIfNotLifetime,
                enabled: !_saving && !_subscriptionLifetime,
              ),
              AppButton(
                label: _saving ? l10n.commonCreating : l10n.adminCreateCompany,
                onPressed: _saving ? null : _create,
              ),
            ],
          ),
        ],
      ),
    );
  }
}
