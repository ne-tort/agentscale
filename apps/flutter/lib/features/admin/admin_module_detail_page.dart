import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Admin module detail — name + multi-cabinet binding.
class AdminModuleDetailPage extends StatefulWidget {
  const AdminModuleDetailPage({
    super.key,
    required this.moduleId,
    required this.moduleName,
  });

  final String moduleId;
  final String moduleName;

  @override
  State<AdminModuleDetailPage> createState() => _AdminModuleDetailPageState();
}

class _AdminModuleDetailPageState extends State<AdminModuleDetailPage> {
  bool _loading = true;
  Object? _error;
  String _name = '';
  Set<String> _cabinetIds = {};
  List<Map<String, dynamic>> _cabinets = const [];

  @override
  void initState() {
    super.initState();
    _name = widget.moduleName;
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final mod = await adminContext.api.getModule(widget.moduleId);
      final cabinets = await adminContext.api.listCabinets();
      if (!mounted) return;
      final ids = mod['cabinet_ids'];
      setState(() {
        _name = mod['name'] as String? ?? widget.moduleName;
        _cabinetIds = ids is List
            ? ids.map((e) => e.toString()).toSet()
            : <String>{};
        _cabinets = cabinets;
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
      final updated = await adminContext.api.updateModule(
        moduleId: widget.moduleId,
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

  Future<void> _saveCabinets(Set<String> cabinetIds) async {
    if (cabinetIds == _cabinetIds) return;
    try {
      final updated = await adminContext.api.updateModule(
        moduleId: widget.moduleId,
        cabinetIds: cabinetIds.toList(),
      );
      if (!mounted) return;
      final ids = updated['cabinet_ids'];
      setState(() {
        _cabinetIds = ids is List
            ? ids.map((e) => e.toString()).toSet()
            : cabinetIds;
      });
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
      rethrow;
    }
  }

  String _cabinetLabel(String id) {
    for (final c in _cabinets) {
      if (c['id'] == id) {
        return c['name'] as String? ?? id;
      }
    }
    return id;
  }

  String _cabinetsSubtitle(Set<String> ids, AppLocalizations l10n) {
    if (ids.isEmpty) return l10n.commonNotSet;
    if (ids.length == 1) return _cabinetLabel(ids.first);
    return l10n.adminModuleCabinetsCount(ids.length);
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final cabinetChoices = _cabinets
        .map((c) => c['id'] as String)
        .where((id) => id.isNotEmpty)
        .toList();
    for (final id in _cabinetIds) {
      if (!cabinetChoices.contains(id)) cabinetChoices.insert(0, id);
    }

    return AppScaffold(
      title: Text(_name.isEmpty ? widget.moduleName : _name),
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
                if (cabinetChoices.isNotEmpty)
                  AppMultiChoicePreference<String>(
                    title: l10n.commonCabinets,
                    icon: Icons.folder_outlined,
                    values: _cabinetIds,
                    choices: cabinetChoices,
                    keyFor: (v) => v,
                    labelFor: _cabinetLabel,
                    presentValues: (ids) => _cabinetsSubtitle(ids, l10n),
                    pickerTitle: l10n.adminSelectCabinetsForModule,
                    onSave: _saveCabinets,
                  ),
              ],
            ),
    );
  }
}
