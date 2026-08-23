import 'package:flutter/material.dart';

import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_form.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_section_header.dart';
import 'package:prodavan/core/widgets/app_text_field.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';

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
    final maxCabinets = _parsePositive(_maxCabinetsCtrl.text);
    final maxPackages = _parsePositive(_maxPackagesCtrl.text);
    final maxBundleMb = _parsePositive(_maxBundleMbCtrl.text);
    if (maxCabinets == null || maxPackages == null || maxBundleMb == null) {
      setState(() => _error = 'Cabinet quotas must be positive integers');
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
    return AppScaffold(
      title: const Text('Create company'),
      body: ListView(
        padding: const EdgeInsets.all(AppSpacing.lg),
        children: [
          if (_error != null) InlineErrorBanner(message: _error!),
          const Text('Invite company.admin via Keycloak — password is not accepted.'),
          const SizedBox(height: AppSpacing.md),
          AppForm(
            formKey: _formKey,
            children: [
              const AppSectionHeader(title: 'Company'),
              AppTextField(
                controller: _nameCtrl,
                label: 'Company name',
                validator: (v) {
                  if (v == null || v.trim().isEmpty) return 'Name required';
                  return null;
                },
              ),
              const AppSectionHeader(title: 'Company admin invite'),
              AppTextField(
                controller: _emailCtrl,
                label: 'Admin email',
                keyboardType: TextInputType.emailAddress,
                validator: (v) {
                  final email = v?.trim() ?? '';
                  if (email.isEmpty || !email.contains('@')) return 'Valid email required';
                  return null;
                },
              ),
              AppTextField(
                controller: _displayNameCtrl,
                label: 'Display name (optional)',
              ),
              const AppSectionHeader(title: 'Cabinet quotas'),
              AppTextField(
                controller: _maxCabinetsCtrl,
                label: 'Max cabinets',
                keyboardType: TextInputType.number,
                validator: (v) => _parsePositive(v ?? '') == null ? 'Positive integer' : null,
              ),
              AppTextField(
                controller: _maxPackagesCtrl,
                label: 'Max packages per cabinet',
                keyboardType: TextInputType.number,
                validator: (v) => _parsePositive(v ?? '') == null ? 'Positive integer' : null,
              ),
              AppTextField(
                controller: _maxBundleMbCtrl,
                label: 'Max bundle import (MB)',
                keyboardType: TextInputType.number,
                validator: (v) => _parsePositive(v ?? '') == null ? 'Positive integer' : null,
              ),
              const AppSectionHeader(title: 'Prodavan subscription (optional)'),
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text('Lifetime subscription'),
                value: _subscriptionLifetime,
                onChanged: _saving ? null : (v) => setState(() => _subscriptionLifetime = v),
              ),
              AppTextField(
                controller: _subscriptionEndsCtrl,
                label: 'Ends at (YYYY-MM-DD, if not lifetime)',
                enabled: !_saving && !_subscriptionLifetime,
              ),
              AppButton(
                label: _saving ? 'Creating…' : 'Create company',
                onPressed: _saving ? null : _create,
              ),
            ],
          ),
        ],
      ),
    );
  }
}
