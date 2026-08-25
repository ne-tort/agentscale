import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/app_subscription_preference.dart';
import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_section_header.dart';
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
  final _descriptionCtrl = TextEditingController();
  final _maxCabinetsCtrl = TextEditingController(text: '10');
  final _maxPackagesCtrl = TextEditingController(text: '20');
  final _maxBundleMbCtrl = TextEditingController(text: '50');
  final _subscriptionEndsCtrl = TextEditingController();

  bool _saving = false;

  static const _defaultMaxCabinets = 10;
  static const _defaultMaxPackages = 20;
  static const _defaultMaxBundleMb = 50;

  @override
  void dispose() {
    _nameCtrl.dispose();
    _emailCtrl.dispose();
    _displayNameCtrl.dispose();
    _descriptionCtrl.dispose();
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
      AppErrors.showSnack(context, l10n.adminCabinetQuotasMustBePositive);
      return;
    }

    final endsRaw = _subscriptionEndsCtrl.text.trim();
    if (endsRaw.isNotEmpty && !AppSubscriptionPreference.isValidDate(endsRaw)) {
      AppErrors.showSnack(context, l10n.adminInvalidDate);
      return;
    }

    setState(() => _saving = true);
    try {
      final displayName = _displayNameCtrl.text.trim();
      final body = await adminContext.api.createCompany(
        name: _nameCtrl.text.trim(),
        adminEmail: _emailCtrl.text.trim(),
        adminDisplayName: displayName.isEmpty ? null : displayName,
        description: _descriptionCtrl.text.trim().isEmpty ? null : _descriptionCtrl.text.trim(),
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

      final endsIso = endsRaw.isEmpty ? null : subscriptionDateToIso(endsRaw);
      await adminContext.api.setCompanySubscription(
        companyId: companyId,
        subscriptionLifetime: endsRaw.isEmpty,
        subscriptionEndsAt: endsIso,
      );

      if (!mounted) return;
      Navigator.of(context).pop(<String, String>{
        'id': companyId,
        'name': companyName,
      });
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
      title: Text(l10n.adminCreateCompany),
      body: ListView(
        padding: EdgeInsets.all(AppSpacing.lg),
        children: [
          Text(l10n.adminInviteCompanyAdminViaKeycloak),
          const SizedBox(height: AppSpacing.md),
          Form(
            key: _formKey,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                AppSectionHeader(title: l10n.commonCompany),
                TextFormField(
                  controller: _nameCtrl,
                  decoration: InputDecoration(labelText: l10n.adminCompanyName),
                  validator: (v) {
                    if (v == null || v.trim().isEmpty) return l10n.commonNameRequired;
                    return null;
                  },
                ),
                const SizedBox(height: AppSpacing.md),
                TextFormField(
                  controller: _descriptionCtrl,
                  decoration: InputDecoration(labelText: l10n.commonDescription),
                  maxLines: 2,
                ),
                const SizedBox(height: AppSpacing.md),
                AppSectionHeader(title: l10n.adminCompanyAdminInvite),
                TextFormField(
                  controller: _emailCtrl,
                  decoration: InputDecoration(labelText: l10n.adminAdminEmail),
                  keyboardType: TextInputType.emailAddress,
                  validator: (v) {
                    final email = v?.trim() ?? '';
                    if (email.isEmpty || !email.contains('@')) return l10n.companyValidEmailRequired;
                    return null;
                  },
                ),
                const SizedBox(height: AppSpacing.md),
                TextFormField(
                  controller: _displayNameCtrl,
                  decoration: InputDecoration(labelText: l10n.commonDisplayNameOptional),
                ),
                const SizedBox(height: AppSpacing.md),
                AppSectionHeader(title: l10n.adminCabinetQuotas),
                TextFormField(
                  controller: _maxCabinetsCtrl,
                  decoration: InputDecoration(labelText: l10n.adminMaxCabinets),
                  keyboardType: TextInputType.number,
                  validator: (v) => _parsePositive(v ?? '') == null ? l10n.commonPositiveInteger : null,
                ),
                const SizedBox(height: AppSpacing.md),
                TextFormField(
                  controller: _maxPackagesCtrl,
                  decoration: InputDecoration(labelText: l10n.adminMaxPackagesPerCabinet),
                  keyboardType: TextInputType.number,
                  validator: (v) => _parsePositive(v ?? '') == null ? l10n.commonPositiveInteger : null,
                ),
                const SizedBox(height: AppSpacing.md),
                TextFormField(
                  controller: _maxBundleMbCtrl,
                  decoration: InputDecoration(labelText: l10n.adminMaxBundleImportMb),
                  keyboardType: TextInputType.number,
                  validator: (v) => _parsePositive(v ?? '') == null ? l10n.commonPositiveInteger : null,
                ),
                const SizedBox(height: AppSpacing.md),
                AppSectionHeader(title: l10n.adminProdavanSubscription),
                TextFormField(
                  controller: _subscriptionEndsCtrl,
                  decoration: InputDecoration(
                    labelText: l10n.adminEndsAt,
                    hintText: l10n.adminDateFormatHint,
                  ),
                  enabled: !_saving,
                ),
                const SizedBox(height: AppSpacing.md),
                AppButton(
                  label: _saving ? l10n.commonCreating : l10n.adminCreateCompany,
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
