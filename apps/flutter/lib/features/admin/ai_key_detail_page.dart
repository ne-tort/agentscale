import 'package:flutter/material.dart';

import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_section_header.dart';
import 'package:prodavan/core/widgets/app_selector_page.dart';
import 'package:prodavan/core/widgets/danger_confirm_page.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';
import 'package:prodavan/features/admin/ai_key_rotate_page.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// AI key detail — status, company bindings (L03/L04).
class AdminAiKeyDetailPage extends StatefulWidget {
  const AdminAiKeyDetailPage({
    super.key,
    required this.keyId,
    required this.keyName,
  });

  final String keyId;
  final String keyName;

  @override
  State<AdminAiKeyDetailPage> createState() => _AdminAiKeyDetailPageState();
}

class _AdminAiKeyDetailPageState extends State<AdminAiKeyDetailPage> {
  bool _loading = true;
  bool _saving = false;
  String? _error;
  Map<String, dynamic>? _key;
  List<Map<String, dynamic>> _companies = const [];

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final key = await adminContext.api.getAiKey(widget.keyId);
      final companies = await adminContext.api.listCompanies();
      if (!mounted) return;
      setState(() {
        _key = key;
        _companies = companies;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  List<String> get _boundIds {
    final ids = _key?['company_ids'];
    if (ids is List) return ids.cast<String>();
    return const [];
  }

  String _companyLabel(String id) {
    for (final c in _companies) {
      if (c['id'] == id) return c['name'] as String? ?? id;
    }
    return id;
  }

  Future<void> _bindCompanies() async {
    final l10n = AppLocalizations.of(context);
    final picked = await Navigator.of(context).push<Set<String>>(
      MaterialPageRoute(
        builder: (_) => AppSelectorPage(
          title: l10n.adminBindCompanies,
          multiSelect: true,
          selectedIds: _boundIds.toSet(),
          showCheckboxes: true,
          items: [
            for (final c in _companies)
              AppSelectorItem(
                id: c['id'] as String,
                title: c['name'] as String? ?? c['id'] as String,
              ),
          ],
          onConfirm: (_) {},
        ),
      ),
    );
    if (picked == null) return;
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await adminContext.api.setAiKeyCompanies(
        keyId: widget.keyId,
        companyIds: picked.toList(),
      );
      await _load();
      if (!mounted) return;
      setState(() => _saving = false);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _saving = false;
      });
    }
  }

  Future<void> _renewKey() async {
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await adminContext.api.renewAiKey(keyId: widget.keyId, months: 1);
      await _load();
      if (!mounted) return;
      setState(() => _saving = false);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _saving = false;
      });
    }
  }

  Future<void> _rotateSecret() async {
    final rotated = await Navigator.of(context).push<bool>(
      MaterialPageRoute<bool>(
        builder: (_) => AdminAiKeyRotatePage(
          keyId: widget.keyId,
          keyName: widget.keyName,
        ),
      ),
    );
    if (rotated == true) await _load();
  }

  Future<void> _disableKey() async {
    final l10n = AppLocalizations.of(context);
    if (_key?['status'] == 'disabled') return;
    final ok = await DangerConfirmPage.push(
      context,
      title: l10n.adminDisableAiKey,
      message: l10n.adminDisableKeyConfirm(widget.keyName),
      confirmLabel: l10n.commonDisable,
    );
    if (!ok) return;
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await adminContext.api.patchAiKey(keyId: widget.keyId, status: 'disabled');
      await _load();
      if (!mounted) return;
      setState(() => _saving = false);
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
      title: Text(widget.keyName),
      actions: [
        IconButton(onPressed: _loading || _saving ? null : _load, icon: const Icon(Icons.refresh)),
      ],
      body: _loading
          ? Center(child: CircularProgressIndicator())
          : ListView(
              padding: EdgeInsets.all(AppSpacing.md),
              children: [
                if (_error != null) InlineErrorBanner(message: _error!),
                AppSectionHeader(title: l10n.adminKeyInfo),
                if (_key != null) ...[
                  Text(l10n.adminProviderValue('${_key!['provider']}')),
                  Text(l10n.adminApiKindValue('${_key!['api_kind']}')),
                  Text(l10n.adminStatusValue('${_key!['status']}')),
                  if (_key!['next_renewal_at'] != null)
                    Text(l10n.adminNextRenewalValue('${_key!['next_renewal_at']}')),
                  Text(l10n.adminSecretRefValue('${_key!['secret_ref_prefix']}')),
                ],
                const SizedBox(height: AppSpacing.lg),
                AppSectionHeader(title: l10n.adminLifecycle),
                AppButton(
                  label: _saving ? l10n.adminRenewing : l10n.adminRenewPlusOneMonth,
                  expanded: false,
                  onPressed: _saving ? null : _renewKey,
                ),
                const SizedBox(height: AppSpacing.sm),
                AppButton(
                  label: l10n.adminRotateSecret,
                  expanded: false,
                  variant: AppButtonVariant.outlined,
                  onPressed: _saving ? null : _rotateSecret,
                ),
                const SizedBox(height: AppSpacing.lg),
                AppSectionHeader(title: l10n.adminCompanyBindings),
                if (_boundIds.isEmpty)
                  EmptyPlaceholder(
                    title: l10n.adminNoCompaniesBound,
                    icon: Icons.link_off_outlined,
                    iconSize: 32,
                    fillViewport: false,
                    padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
                  )
                else
                  ..._boundIds.map((id) => ListTile(title: Text(_companyLabel(id)), subtitle: Text(id))),
                AppButton(
                  label: _saving ? l10n.commonSaving : l10n.adminEditBindings,
                  expanded: false,
                  onPressed: _saving ? null : _bindCompanies,
                ),
                const SizedBox(height: AppSpacing.lg),
                if (_key?['status'] != 'disabled')
                  AppButton(
                    label: l10n.adminDisableKey,
                    expanded: false,
                    variant: AppButtonVariant.outlined,
                    onPressed: _saving ? null : _disableKey,
                  ),
              ],
            ),
    );
  }
}
