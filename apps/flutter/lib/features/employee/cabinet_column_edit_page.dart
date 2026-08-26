import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/theme/app_color_tokens.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_confirm_page.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Edit column metadata / type (L06 PATCH columns).
class CabinetColumnEditPage extends StatefulWidget {
  const CabinetColumnEditPage({
    super.key,
    required this.cabinetId,
    required this.tableSlug,
    required this.tableLabel,
    required this.column,
  });

  final String cabinetId;
  final String tableSlug;
  final String tableLabel;
  final Map<String, dynamic> column;

  @override
  State<CabinetColumnEditPage> createState() => _CabinetColumnEditPageState();
}

class _CabinetColumnEditPageState extends State<CabinetColumnEditPage> {
  static const _columnTypes = ['text', 'number', 'bool', 'datetime', 'json', 'enum', 'ref', 'file_ref'];
  static const _protectedNames = {'id', 'created_at'};

  late String _type;
  late bool _required;
  late bool _unique;
  bool _saving = false;
  bool _deleting = false;
  String? _error;

  String get _columnName => widget.column['name'] as String? ?? '';

  bool get _isProtected => _protectedNames.contains(_columnName);

  @override
  void initState() {
    super.initState();
    _type = widget.column['type'] as String? ?? 'text';
    _required = widget.column['required'] as bool? ?? false;
    _unique = widget.column['unique'] as bool? ?? false;
  }

  Future<void> _save() async {
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await workContext.api.updateMetaColumn(
        cabinetId: widget.cabinetId,
        tableSlug: widget.tableSlug,
        columnName: _columnName,
        type: _type,
        required: _required,
        unique: _unique,
      );
      if (!mounted) return;
      Navigator.of(context).pop(true);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _saving = false;
      });
    }
  }

  Future<void> _delete() async {
    final l10n = AppLocalizations.of(context);
    final ok = await AppConfirmPage.push(
      context,
      title: l10n.cabinetDeleteColumnConfirm,
      message: l10n.cabinetRemoveColumnData(_columnName),
      confirmLabel: l10n.commonDelete,
      severity: AppStatusSeverity.error,
    );
    if (ok != true) return;

    setState(() {
      _deleting = true;
      _error = null;
    });
    try {
      await workContext.api.deleteMetaColumn(
        cabinetId: widget.cabinetId,
        tableSlug: widget.tableSlug,
        columnName: _columnName,
      );
      if (!mounted) return;
      Navigator.of(context).pop(true);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _deleting = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final busy = _saving || _deleting;
    return AppScaffold(
      title: Text(l10n.cabinetColumnSettings),
      body: ListView(
        padding: const EdgeInsets.all(AppSpacing.lg),
        children: [
          Text(widget.tableLabel, style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: AppSpacing.sm),
          Text(_columnName, style: Theme.of(context).textTheme.titleSmall),
          if (_isProtected)
            Padding(
              padding: EdgeInsets.only(top: AppSpacing.sm),
              child: Text(
                l10n.cabinetSystemColumnReadOnly,
                style: Theme.of(context).textTheme.bodySmall?.copyWith(
                      color: context.appColors.muted,
                    ),
              ),
            ),
          if (_error != null) ...[
            const SizedBox(height: AppSpacing.sm),
            AppStatusBanner(severity: AppStatusSeverity.error, message: _error!),
          ],
          if (!_isProtected) ...[
            const SizedBox(height: AppSpacing.md),
            AppChoicePreference<String>(
              title: l10n.cabinetType,
              icon: Icons.category_outlined,
              value: _type,
              choices: _columnTypes,
              keyFor: (v) => v,
              labelFor: (v) => v,
              enabled: !busy,
              onSave: (v) async => setState(() => _type = v),
            ),
            AppSwitchPreference(
              title: l10n.commonRequired,
              icon: Icons.star_outline,
              value: _required,
              enabled: !busy,
              onChanged: (v) async => setState(() => _required = v),
            ),
            AppSwitchPreference(
              title: l10n.cabinetUnique,
              icon: Icons.fingerprint_outlined,
              value: _unique,
              enabled: !busy,
              onChanged: (v) async => setState(() => _unique = v),
            ),
            AppButton(
              label: _saving ? l10n.commonSaving : l10n.commonSave,
              onPressed: busy ? null : _save,
            ),
            Divider(height: 32),
            AppButton(
              label: _deleting ? l10n.commonDeleting : l10n.cabinetDeleteColumn,
              onPressed: busy ? null : _delete,
            ),
          ],
        ],
      ),
    );
  }
}
