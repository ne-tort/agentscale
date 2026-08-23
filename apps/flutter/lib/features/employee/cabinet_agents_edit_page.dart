import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_button.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/inline_error_banner.dart';

/// Edit cabinet AGENTS.md source (materialize writes AGENTS.md + CLAUDE.md).
class CabinetAgentsEditPage extends StatefulWidget {
  const CabinetAgentsEditPage({
    super.key,
    required this.cabinetId,
  });

  final String cabinetId;

  @override
  State<CabinetAgentsEditPage> createState() => _CabinetAgentsEditPageState();
}

class _CabinetAgentsEditPageState extends State<CabinetAgentsEditPage> {
  final _ctrl = TextEditingController();
  bool _loading = true;
  bool _saving = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final doc = await workContext.api.getWorkspaceDoc(
        cabinetId: widget.cabinetId,
        slug: 'agents',
      );
      if (!mounted) return;
      _ctrl.text = doc['body'] as String? ?? '';
      setState(() => _loading = false);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  Future<void> _save() async {
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await workContext.api.putWorkspaceDoc(
        cabinetId: widget.cabinetId,
        slug: 'agents',
        body: _ctrl.text,
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
      title: const Text('AGENTS.md'),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.all(AppSpacing.lg),
              children: [
                Text(
                  'Written into project workspace on materialize (AGENTS.md + CLAUDE.md). Re-materialize projects to apply.',
                  style: Theme.of(context).textTheme.bodySmall?.copyWith(
                        color: Theme.of(context).colorScheme.onSurfaceVariant,
                      ),
                ),
                const SizedBox(height: AppSpacing.md),
                if (_error != null) InlineErrorBanner(message: _error!),
                TextField(
                  controller: _ctrl,
                  maxLines: 18,
                  enabled: !_saving,
                  decoration: const InputDecoration(
                    border: OutlineInputBorder(),
                    alignLabelWithHint: true,
                    labelText: 'Agents instructions',
                  ),
                ),
                const SizedBox(height: AppSpacing.md),
                AppButton(
                  label: _saving ? 'Saving…' : 'Save',
                  onPressed: _saving ? null : _save,
                ),
              ],
            ),
    );
  }
}
