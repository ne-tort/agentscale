import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_form.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_text_field.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Create custom view + tab bound to a meta table (L06).
class CabinetMetaTabCreatePage extends StatefulWidget {
  const CabinetMetaTabCreatePage({super.key, required this.cabinetId});

  final String cabinetId;

  @override
  State<CabinetMetaTabCreatePage> createState() => _CabinetMetaTabCreatePageState();
}

class _CabinetMetaTabCreatePageState extends State<CabinetMetaTabCreatePage> {
  final _formKey = GlobalKey<FormState>();
  final _tabTitle = TextEditingController();
  final _viewSlug = TextEditingController();
  final _order = TextEditingController(text: '60');
  List<Map<String, dynamic>> _tables = const [];
  String? _tableSlug;
  bool _loadingTables = true;
  bool _saving = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadTables();
  }

  @override
  void dispose() {
    _tabTitle.dispose();
    _viewSlug.dispose();
    _order.dispose();
    super.dispose();
  }

  Future<void> _loadTables() async {
    try {
      final tables = await workContext.api.listMetaTables(widget.cabinetId);
      if (!mounted) return;
      setState(() {
        _tables = tables;
        _tableSlug = tables.isNotEmpty ? tables.first['slug'] as String? : null;
        _loadingTables = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loadingTables = false;
      });
    }
  }

  Future<void> _save() async {
    if (!_formKey.currentState!.validate()) return;
    final l10n = AppLocalizations.of(context);
    if (_tableSlug == null || _tableSlug!.isEmpty) {
      setState(() => _error = l10n.cabinetSelectATable);
      return;
    }

    final title = _tabTitle.text.trim();
    final slug = _viewSlug.text.trim();
    final order = int.tryParse(_order.text.trim()) ?? 60;

    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await workContext.api.createMetaView(
        cabinetId: widget.cabinetId,
        slug: slug,
        tableSlug: _tableSlug,
        uiJson: {
          'version': 1,
          'kind': 'collection',
          'title_field': 'title',
        },
      );
      await workContext.api.createMetaTab(
        cabinetId: widget.cabinetId,
        title: title,
        order: order,
        viewSlug: slug,
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

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
if (_loadingTables) {
      return AppScaffold(
        title: Text(l10n.cabinetNewCustomTab),
        body: Center(child: CircularProgressIndicator()),
      );
    }

    return AppScaffold(
      title: Text(l10n.cabinetNewCustomTab),
      body: ListView(
        padding: EdgeInsets.all(AppSpacing.lg),
        children: [
          if (_error != null) InlineErrorBanner(message: _error!),
          if (_tables.isEmpty)
            Text(l10n.cabinetCreateMetaTableFirst)
          else
            AppForm(
              formKey: _formKey,
              children: [
                AppTextField(
                  controller: _tabTitle,
                  label: l10n.cabinetTabTitle,
                  enabled: !_saving,
                  validator: (v) => (v ?? '').trim().isEmpty ? l10n.commonRequired : null,
                ),
                AppTextField(
                  controller: _viewSlug,
                  label: l10n.cabinetViewSlug,
                  enabled: !_saving,
                  validator: (v) {
                    final s = (v ?? '').trim();
                    if (s.isEmpty) return l10n.commonRequired;
                    if (!RegExp(r'^[a-z][a-z0-9_]*$').hasMatch(s)) {
                      return l10n.cabinetLowercaseSlugRule;
                    }
                    return null;
                  },
                ),
                DropdownButtonFormField<String>(
                  value: _tableSlug,
                  decoration: InputDecoration(labelText: l10n.commonTable),
                  items: [
                    for (final t in _tables)
                      DropdownMenuItem(
                        value: t['slug'] as String?,
                        child: Text(t['label'] as String? ?? '${t['slug']}'),
                      ),
                  ],
                  onChanged: _saving ? null : (v) => setState(() => _tableSlug = v),
                ),
                AppTextField(
                  controller: _order,
                  label: l10n.cabinetTabOrder,
                  enabled: !_saving,
                  keyboardType: TextInputType.number,
                ),
                AppButton(
                  label: _saving ? l10n.commonCreating : l10n.cabinetCreateTab,
                  onPressed: _saving ? null : _save,
                ),
              ],
            ),
        ],
      ),
    );
  }
}
