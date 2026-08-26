import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Admin cabinet detail — name + company binding (seamless preferences).
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
  String _companyId = '';
  String _companyName = '';
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
      setState(() {
        _name = cab['name'] as String? ?? widget.cabinetName;
        _companyId = cab['company_id'] as String? ?? '';
        _companyName = cab['company_name'] as String? ??
            cab['company_id'] as String? ??
            '';
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

  Future<void> _saveCompany(String companyId) async {
    if (companyId.isEmpty || companyId == _companyId) return;
    try {
      final updated = await adminContext.api.updateCabinet(
        cabinetId: widget.cabinetId,
        companyId: companyId,
      );
      if (!mounted) return;
      setState(() {
        _companyId = updated['company_id'] as String? ?? companyId;
        _companyName = updated['company_name'] as String? ??
            _companyLabel(companyId);
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

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final companyChoices = _companies
        .map((c) => c['id'] as String)
        .where((id) => id.isNotEmpty)
        .toList();
    if (_companyId.isNotEmpty && !companyChoices.contains(_companyId)) {
      companyChoices.insert(0, _companyId);
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
                AppValuePreference<String>(
                  title: l10n.commonName,
                  icon: Icons.label_outline_rounded,
                  value: _name,
                  validateInput: (v) => v.trim().isNotEmpty,
                  onSave: _saveName,
                ),
                if (companyChoices.isNotEmpty)
                  AppChoicePreference<String>(
                    title: l10n.commonCompany,
                    icon: Icons.business_outlined,
                    value: _companyId.isEmpty ? companyChoices.first : _companyId,
                    choices: companyChoices,
                    keyFor: (v) => v,
                    labelFor: (id) =>
                        id == _companyId && _companyName.isNotEmpty
                            ? _companyName
                            : _companyLabel(id),
                    presentValue: (id) =>
                        id == _companyId && _companyName.isNotEmpty
                            ? _companyName
                            : _companyLabel(id),
                    pickerTitle: l10n.adminSelectCompanyForCabinet,
                    onSave: _saveCompany,
                  ),
              ],
            ),
    );
  }
}
