import 'package:flutter/material.dart';

import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_form.dart';
import 'package:prodavan/core/widgets/app_password_field.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_section_header.dart';
import 'package:prodavan/core/widgets/app_selector_page.dart';
import 'package:prodavan/core/widgets/app_text_field.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Create AI provider key + optional company bind (L03/L04).
class AdminAiKeyCreatePage extends StatefulWidget {
  const AdminAiKeyCreatePage({super.key});

  @override
  State<AdminAiKeyCreatePage> createState() => _AdminAiKeyCreatePageState();
}

class _AdminAiKeyCreatePageState extends State<AdminAiKeyCreatePage> {
  final _formKey = GlobalKey<FormState>();
  final _nameCtrl = TextEditingController();
  final _secretCtrl = TextEditingController();
  String _provider = 'cursor';
  String _apiKind = 'cursor_sdk';
  List<String> _companyIds = const [];
  List<Map<String, dynamic>> _companies = const [];
  bool _saving = false;
  String? _error;

  static const _providers = ['cursor', 'codex', 'claude_code'];
  static const _apiKinds = [
    'cursor_sdk',
    'codex_sdk',
    'claude_agent_sdk',
    'openai_api',
    'anthropic_api',
  ];

  @override
  void initState() {
    super.initState();
    _loadCompanies();
  }

  @override
  void dispose() {
    _nameCtrl.dispose();
    _secretCtrl.dispose();
    super.dispose();
  }

  Future<void> _loadCompanies() async {
    try {
      final items = await adminContext.api.listCompanies();
      if (!mounted) return;
      setState(() => _companies = items);
    } catch (_) {}
  }

  Future<void> _pickCompanies() async {
    final l10n = AppLocalizations.of(context);
    final picked = await Navigator.of(context).push<Set<String>>(
      MaterialPageRoute(
        builder: (_) => AppSelectorPage(
          title: l10n.adminBindCompanies,
          multiSelect: true,
          selectedIds: _companyIds.toSet(),
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
    setState(() => _companyIds = picked.toList());
  }

  Future<void> _save() async {
    if (!_formKey.currentState!.validate()) return;
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await adminContext.api.createAiKey(
        name: _nameCtrl.text.trim(),
        provider: _provider,
        apiKind: _apiKind,
        secret: _secretCtrl.text,
        companyIds: _companyIds,
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
    return AppScaffold(
      title: Text(l10n.adminCreateAiKey),
      body: ListView(
        padding: EdgeInsets.all(AppSpacing.md),
        children: [
          if (_error != null) InlineErrorBanner(message: _error!),
          AppSectionHeader(title: l10n.adminKeyMetadata, subtitle: l10n.adminSecretStoredServerSide),
          AppForm(
            formKey: _formKey,
            children: [
              AppTextField(
                controller: _nameCtrl,
                label: l10n.commonName,
                validator: (v) => (v == null || v.trim().isEmpty) ? l10n.commonRequired : null,
              ),
              InputDecorator(
                decoration: InputDecoration(labelText: l10n.commonProvider),
                child: DropdownButtonHideUnderline(
                  child: DropdownButton<String>(
                    value: _provider,
                    isExpanded: true,
                    items: [
                      for (final p in _providers) DropdownMenuItem(value: p, child: Text(p)),
                    ],
                    onChanged: _saving ? null : (v) => setState(() => _provider = v ?? 'cursor'),
                  ),
                ),
              ),
              InputDecorator(
                decoration: InputDecoration(labelText: l10n.adminApiKind),
                child: DropdownButtonHideUnderline(
                  child: DropdownButton<String>(
                    value: _apiKinds.contains(_apiKind) ? _apiKind : 'cursor_sdk',
                    isExpanded: true,
                    items: [
                      for (final k in _apiKinds) DropdownMenuItem(value: k, child: Text(k)),
                    ],
                    onChanged: _saving ? null : (v) => setState(() => _apiKind = v ?? 'cursor_sdk'),
                  ),
                ),
              ),
              AppPasswordField(
                controller: _secretCtrl,
                label: l10n.commonSecret,
                validator: (v) => (v == null || v.isEmpty) ? l10n.commonRequired : null,
              ),
              ListTile(
                contentPadding: EdgeInsets.zero,
                title: Text(l10n.adminCompanyBindings),
                subtitle: Text(
                  _companyIds.isEmpty ? l10n.commonNone : '${_companyIds.length} selected',
                ),
                trailing: TextButton(onPressed: _saving ? null : _pickCompanies, child: Text(l10n.commonSelect)),
              ),
              AppButton(
                label: _saving ? l10n.commonCreating : l10n.adminCreateKey,
                expanded: false,
                onPressed: _saving ? null : _save,
              ),
            ],
          ),
        ],
      ),
    );
  }
}
