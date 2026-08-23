import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_form.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_section_header.dart';
import 'package:prodavan/core/widgets/app_text_field.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';
import 'package:prodavan/features/employee/dynamic_cabinet_shell.dart';

/// Import cabinet.bundle zip into a new schema (L05/L06 C-BUNDLE).
class CabinetImportBundlePage extends StatefulWidget {
  const CabinetImportBundlePage({super.key});

  @override
  State<CabinetImportBundlePage> createState() => _CabinetImportBundlePageState();
}

class _CabinetImportBundlePageState extends State<CabinetImportBundlePage> {
  final _formKey = GlobalKey<FormState>();
  final _nameCtrl = TextEditingController();
  List<int>? _zipBytes;
  String? _pickedFilename;
  bool _importing = false;
  String? _error;

  @override
  void dispose() {
    _nameCtrl.dispose();
    super.dispose();
  }

  Future<void> _pickBundle() async {
    final result = await FilePicker.platform.pickFiles(
      type: FileType.custom,
      allowedExtensions: const ['zip'],
      withData: true,
    );
    if (result == null || result.files.isEmpty) return;
    final file = result.files.first;
    final bytes = file.bytes;
    if (bytes == null) {
      setState(() => _error = 'Could not read zip bytes');
      return;
    }
    setState(() {
      _zipBytes = bytes;
      _pickedFilename = file.name;
      _error = null;
      if (_nameCtrl.text.trim().isEmpty) {
        final base = file.name.replaceAll(RegExp(r'\.zip$', caseSensitive: false), '');
        _nameCtrl.text = base.isEmpty ? 'Imported cabinet' : base;
      }
    });
  }

  Future<void> _import() async {
    if (!_formKey.currentState!.validate()) return;
    final companyId = workContext.companyId;
    if (companyId == null) {
      setState(() => _error = 'No company_id from /me memberships');
      return;
    }
    final bytes = _zipBytes;
    if (bytes == null || bytes.isEmpty) {
      setState(() => _error = 'Select a cabinet.bundle zip file');
      return;
    }

    setState(() {
      _importing = true;
      _error = null;
    });
    try {
      final result = await workContext.api.importCabinetBundle(
        companyId: companyId,
        zipBytes: bytes,
        name: _nameCtrl.text.trim(),
      );
      if (!mounted) return;
      final cabinet = result['cabinet'] as Map<String, dynamic>? ?? result;
      final cabinetId = cabinet['id'] as String;
      final cabinetName = cabinet['name'] as String? ?? _nameCtrl.text.trim();
      workContext.enterCabinet(cabinetId);
      Navigator.of(context).pushReplacement(
        MaterialPageRoute<void>(
          builder: (_) => DynamicCabinetShell(cabinetId: cabinetId, cabinetName: cabinetName),
        ),
      );
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _importing = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return AppScaffold(
      title: const Text('Import cabinet bundle'),
      body: ListView(
        padding: const EdgeInsets.all(AppSpacing.lg),
        children: [
          if (_error != null) InlineErrorBanner(message: _error!),
          const Text(
            'Import a cabinet.bundle zip exported from another cabinet. '
            'Creates a new cabinet instance with a fresh schema.',
          ),
          const SizedBox(height: AppSpacing.md),
          AppForm(
            formKey: _formKey,
            children: [
              OutlinedButton.icon(
                onPressed: _importing ? null : _pickBundle,
                icon: const Icon(Icons.folder_open),
                label: Text(_pickedFilename ?? 'Choose .zip file'),
              ),
              const SizedBox(height: AppSpacing.md),
              const AppSectionHeader(title: 'New cabinet'),
              AppTextField(
                controller: _nameCtrl,
                label: 'Cabinet name',
                enabled: !_importing,
                validator: (v) {
                  if (v == null || v.trim().isEmpty) return 'Name required';
                  return null;
                },
              ),
              AppButton(
                label: _importing ? 'Importing…' : 'Import bundle',
                onPressed: _importing ? null : _import,
              ),
            ],
          ),
        ],
      ),
    );
  }
}
