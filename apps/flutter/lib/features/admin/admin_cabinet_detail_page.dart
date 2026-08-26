import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Admin cabinet detail — name, multi-company grants, owner_scope badge.
class AdminCabinetDetailPage extends StatefulWidget {
  const AdminCabinetDetailPage({
    super.key,
    required this.cabinetId,
    required this.cabinetName,
  });

  final String cabinetId;
  final String cabinetName;

  @override
  State<AdminCabinetDetailPage> createState() => _AdminCabinetDetailPageState();
}

class _AdminCabinetDetailPageState extends State<AdminCabinetDetailPage> {
  bool _loading = true;
  Object? _error;
  String _name = '';
  String _ownerScope = 'platform';
  Set<String> _companyIds = {};
  List<Map<String, dynamic>> _companies = const [];

  @override
  void initState() {
    super.initState();
    _name = widget.cabinetName;
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final cab = await adminContext.api.getCabinet(widget.cabinetId);
      final companies = await adminContext.api.listCompanies();
      if (!mounted) return;
      final ids = cab['company_ids'];
      setState(() {
        _name = cab['name'] as String? ?? widget.cabinetName;
        _ownerScope = cab['owner_scope'] as String? ?? 'platform';
        _companyIds = ids is List
            ? ids.map((e) => e.toString()).toSet()
            : <String>{};
        _companies = companies;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e;
        _loading = false;
      });
    }
  }

  Future<void> _saveName(String value) async {
    final trimmed = value.trim();
    if (trimmed.isEmpty || trimmed == _name) return;
    try {
      final updated = await adminContext.api.updateCabinet(
        cabinetId: widget.cabinetId,
        name: trimmed,
      );
      if (!mounted) return;
      setState(() {
        _name = updated['name'] as String? ?? trimmed;
      });
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
      rethrow;
    }
  }

  Future<void> _saveCompanies(Set<String> companyIds) async {
    if (companyIds.isEmpty || companyIds == _companyIds) return;
    try {
      final updated = await adminContext.api.updateCabinet(
        cabinetId: widget.cabinetId,
        companyIds: companyIds.toList(),
      );
      if (!mounted) return;
      final ids = updated['company_ids'];
      setState(() {
        _companyIds = ids is List
            ? ids.map((e) => e.toString()).toSet()
            : companyIds;
      });
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
      rethrow;
    }
  }

  String _companyLabel(String id) {
    for (final c in _companies) {
      if (c['id'] == id) {
        return c['name'] as String? ?? id;
      }
    }
    return id;
  }

  String _companiesSubtitle(Set<String> ids, AppLocalizations l10n) {
    if (ids.isEmpty) return l10n.commonNotSet;
    if (ids.length == 1) return _companyLabel(ids.first);
    return l10n.adminCabinetCompaniesCount(ids.length);
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final companyChoices = _companies
        .map((c) => c['id'] as String)
        .where((id) => id.isNotEmpty)
        .toList();
    for (final id in _companyIds) {
      if (!companyChoices.contains(id)) companyChoices.insert(0, id);
    }

    return AppScaffold(
      title: Text(_name.isEmpty ? widget.cabinetName : _name),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
              children: [
                if (_error != null)
                  AppStatusBanner(
                    severity: AppStatusSeverity.error,
                    message: AppErrors.localize(context, _error!),
                  ),
                ListTile(
                  leading: const Icon(Icons.shield_outlined),
                  title: Text(l10n.adminCabinetOwnerScope),
                  subtitle: Text(_ownerScope),
                ),
                AppValuePreference<String>(
                  title: l10n.commonName,
                  icon: Icons.label_outline_rounded,
                  value: _name,
                  validateInput: (v) => v.trim().isNotEmpty,
                  onSave: _saveName,
                ),
                if (companyChoices.isNotEmpty)
                  AppMultiChoicePreference<String>(
                    title: l10n.commonCompanies,
                    icon: Icons.business_outlined,
                    values: _companyIds,
                    choices: companyChoices,
                    keyFor: (v) => v,
                    labelFor: _companyLabel,
                    presentValues: (ids) => _companiesSubtitle(ids, l10n),
                    pickerTitle: l10n.adminSelectCompanyForCabinet,
                    onSave: _saveCompanies,
                  ),
              ],
            ),
    );
  }
}
