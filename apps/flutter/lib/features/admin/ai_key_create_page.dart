import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_section_header.dart';
import 'package:prodavan/core/widgets/app_selector_page.dart';
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
  bool _obscureSecret = true;
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
          Form(
            key: _formKey,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                TextFormField(
                  controller: _nameCtrl,
                  decoration: InputDecoration(labelText: l10n.commonName),
                  validator: (v) => (v == null || v.trim().isEmpty) ? l10n.commonRequired : null,
                ),
                const SizedBox(height: AppSpacing.md),
                AppChoicePreference<String>(
                  title: l10n.commonProvider,
                  icon: Icons.cloud_outlined,
                  value: _provider,
                  choices: _providers,
                  keyFor: (v) => v,
                  labelFor: (v) => v,
                  enabled: !_saving,
                  onSave: (v) async => setState(() => _provider = v),
                ),
                AppChoicePreference<String>(
                  title: l10n.adminApiKind,
                  icon: Icons.api_outlined,
                  value: _apiKinds.contains(_apiKind) ? _apiKind : 'cursor_sdk',
                  choices: _apiKinds,
                  keyFor: (v) => v,
                  labelFor: (v) => v,
                  enabled: !_saving,
                  onSave: (v) async => setState(() => _apiKind = v),
                ),
                TextFormField(
                  controller: _secretCtrl,
                  obscureText: _obscureSecret,
                  autofillHints: const [AutofillHints.password],
                  decoration: InputDecoration(
                    labelText: l10n.commonSecret,
                    suffixIcon: IconButton(
                      icon: Icon(
                        _obscureSecret ? Icons.visibility_outlined : Icons.visibility_off_outlined,
                      ),
                      onPressed: () => setState(() => _obscureSecret = !_obscureSecret),
                    ),
                  ),
                  validator: (v) => (v == null || v.isEmpty) ? l10n.commonRequired : null,
                ),
                const SizedBox(height: AppSpacing.md),
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
          ),
        ],
      ),
    );
  }
}
