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
    final picked = await Navigator.of(context).push<Set<String>>(
      MaterialPageRoute(
        builder: (_) => AppSelectorPage(
          title: 'Bind companies',
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
    return AppScaffold(
      title: const Text('Create AI key'),
      body: ListView(
        padding: const EdgeInsets.all(AppSpacing.md),
        children: [
          if (_error != null) InlineErrorBanner(message: _error!),
          const AppSectionHeader(title: 'Key metadata', subtitle: 'Secret is stored server-side only'),
          AppForm(
            formKey: _formKey,
            children: [
              AppTextField(
                controller: _nameCtrl,
                label: 'Name',
                validator: (v) => (v == null || v.trim().isEmpty) ? 'Required' : null,
              ),
              InputDecorator(
                decoration: const InputDecoration(labelText: 'Provider'),
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
                decoration: const InputDecoration(labelText: 'API kind'),
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
                label: 'Secret',
                validator: (v) => (v == null || v.isEmpty) ? 'Required' : null,
              ),
              ListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text('Company bindings'),
                subtitle: Text(
                  _companyIds.isEmpty ? 'None' : '${_companyIds.length} selected',
                ),
                trailing: TextButton(onPressed: _saving ? null : _pickCompanies, child: const Text('Select')),
              ),
              AppButton(
                label: _saving ? 'Creating…' : 'Create key',
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
