import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_form.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_text_field.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';

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
    if (_tableSlug == null || _tableSlug!.isEmpty) {
      setState(() => _error = 'Select a table');
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
    if (_loadingTables) {
      return const AppScaffold(
        title: Text('New custom tab'),
        body: Center(child: CircularProgressIndicator()),
      );
    }

    return AppScaffold(
      title: const Text('New custom tab'),
      body: ListView(
        padding: const EdgeInsets.all(AppSpacing.lg),
        children: [
          if (_error != null) InlineErrorBanner(message: _error!),
          if (_tables.isEmpty)
            const Text('Create a meta table first (Tables tab → New table).')
          else
            AppForm(
              formKey: _formKey,
              children: [
                AppTextField(
                  controller: _tabTitle,
                  label: 'Tab title',
                  enabled: !_saving,
                  validator: (v) => (v ?? '').trim().isEmpty ? 'Required' : null,
                ),
                AppTextField(
                  controller: _viewSlug,
                  label: 'View slug',
                  enabled: !_saving,
                  validator: (v) {
                    final s = (v ?? '').trim();
                    if (s.isEmpty) return 'Required';
                    if (!RegExp(r'^[a-z][a-z0-9_]*$').hasMatch(s)) {
                      return 'Lowercase letters, digits, underscore';
                    }
                    return null;
                  },
                ),
                DropdownButtonFormField<String>(
                  value: _tableSlug,
                  decoration: const InputDecoration(labelText: 'Table'),
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
                  label: 'Tab order',
                  enabled: !_saving,
                  keyboardType: TextInputType.number,
                ),
                AppButton(
                  label: _saving ? 'Creating…' : 'Create tab',
                  onPressed: _saving ? null : _save,
                ),
              ],
            ),
        ],
      ),
    );
  }
}
