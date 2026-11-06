import 'dart:convert';

import 'package:flutter/material.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/l10n/app_localizations.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_confirm_page.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_status_banner.dart';

/// CRUD for project-owned cron schedules.
class ProjectSchedulesPage extends StatefulWidget {
  const ProjectSchedulesPage({
    super.key,
    required this.projectId,
  });

  final String projectId;

  @override
  State<ProjectSchedulesPage> createState() => _ProjectSchedulesPageState();
}

class _ProjectSchedulesPageState extends State<ProjectSchedulesPage> {
  List<Map<String, dynamic>> _items = const [];
  bool _loading = true;
  bool _saving = false;
  final Set<String> _togglingIds = <String>{};

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    if (mounted) setState(() => _loading = true);
    try {
      final schedules = await workContext.api.listProjectSchedules(widget.projectId);
      if (mounted) setState(() { _items = schedules; _loading = false; });
    } catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      AppErrors.showSnack(context, e);
    }
  }

  Future<void> _edit([Map<String, dynamic>? schedule]) async {
    // A full page, not a dialog: the editor carries four fields plus a switch,
    // which a modal clips on a phone, and lib/features keeps every editor and
    // picker on the navigation stack (see AppConfirmPage / AppSelectorPage).
    final result = await Navigator.of(context).push<_ScheduleDraft>(
      MaterialPageRoute(builder: (_) => _ScheduleEditorPage(schedule: schedule)),
    );
    if (result == null || !mounted) return;
    setState(() => _saving = true);
    try {
      if (schedule == null) {
        await workContext.api.createProjectSchedule(
          projectId: widget.projectId,
          name: result.name,
          cron: result.cron,
          timezone: result.timezone,
          payload: result.payload,
          enabled: result.enabled,
        );
      } else {
        await workContext.api.patchProjectSchedule(
          projectId: widget.projectId,
          scheduleId: schedule['id'] as String,
          name: result.name,
          cron: result.cron,
          timezone: result.timezone,
          payload: result.payload,
          enabled: result.enabled,
        );
      }
      await _load();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _toggle(Map<String, dynamic> item, bool enabled) async {
    final scheduleId = item['id'] as String;
    if (_togglingIds.contains(scheduleId)) return;
    setState(() => _togglingIds.add(scheduleId));
    try {
      final updated = await workContext.api.patchProjectSchedule(
        projectId: widget.projectId,
        scheduleId: scheduleId,
        enabled: enabled,
      );
      if (!mounted) return;
      // The PATCH response is authoritative: it includes next_run_at, which
      // changes when the server accepts an enable/disable transition. Re-read
      // the list afterwards so the row matches the server's stored schedule.
      setState(() {
        final index = _items.indexWhere((entry) => entry['id'] == scheduleId);
        if (index >= 0) {
          final nextItems = List<Map<String, dynamic>>.of(_items);
          nextItems[index] = updated;
          _items = nextItems;
        }
      });
      await _load();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    } finally {
      if (mounted) setState(() => _togglingIds.remove(scheduleId));
    }
  }

  Future<void> _delete(Map<String, dynamic> item) async {
    final l10n = AppLocalizations.of(context);
    final scheduleId = item['id'] as String;
    final ok = await AppConfirmPage.push(
      context,
      title: l10n.scheduleDeleteConfirmTitle,
      message: l10n.scheduleDeleteConfirmMessage,
      confirmLabel: l10n.scheduleDelete,
      severity: AppStatusSeverity.warning,
    );
    if (!ok || !mounted) return;
    try {
      await workContext.api.deleteProjectSchedule(
        projectId: widget.projectId,
        scheduleId: scheduleId,
      );
      await _load();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  Future<void> _refresh() => _load();

  String? _missedRunsWarning(
    Map<String, dynamic> item,
    AppLocalizations l10n,
  ) {
    final payload = item['payload'];
    if (payload is! Map || payload['missed_count_capped'] != true) {
      return null;
    }
    final lowerBound = payload['missed_count_lower_bound'];
    final count = lowerBound is num ? lowerBound.toInt() : null;
    return l10n.scheduleMissedRunsWarning(count ?? 0);
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return AppScaffold(
      title: Text(l10n.scheduleTitle),
      actions: [
        IconButton(
          tooltip: l10n.scheduleAdd,
          onPressed: _saving ? null : () => _edit(),
          icon: const Icon(Icons.add),
        ),
      ],
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : RefreshIndicator(
              onRefresh: _refresh,
              child: _items.isEmpty
                  ? ListView(
                      padding: EdgeInsets.all(AppSpacing.md),
                      children: [
                        const SizedBox(height: 72),
                        Icon(Icons.schedule_outlined, size: 48, color: Theme.of(context).colorScheme.outline),
                        const SizedBox(height: 12),
                        Center(child: Text(l10n.scheduleNoSchedules)),
                        const SizedBox(height: 12),
                        Center(child: OutlinedButton.icon(
                          onPressed: _saving ? null : () => _edit(),
                          icon: const Icon(Icons.add),
                          label: Text(l10n.scheduleCreate),
                        )),
                      ],
                    )
                  : ListView.separated(
                      padding: EdgeInsets.all(AppSpacing.md),
                      itemCount: _items.length,
                      separatorBuilder: (_, __) => const Divider(height: 1),
                      itemBuilder: (context, index) {
                        final item = _items[index];
                        final enabled = item['enabled'] == true;
                        final payload = item['payload'] is Map
                            ? const JsonEncoder.withIndent('  ').convert(item['payload'])
                            : '{}';
                        final nextRun = item['next_run_at'] ?? l10n.scheduleNotScheduled;
                        final missedRunsWarning = _missedRunsWarning(item, l10n);
                        return ListTile(
                          contentPadding: EdgeInsets.zero,
                          leading: Icon(enabled ? Icons.schedule : Icons.schedule_outlined),
                          title: Text(item['name'] as String? ?? ''),
                          subtitle: Padding(
                            padding: const EdgeInsets.only(top: 6),
                            child: Text(
                              '${item['cron'] ?? ''} · ${item['timezone'] ?? 'UTC'}\n'
                              '${l10n.scheduleNextRun}: $nextRun\n'
                              '${l10n.schedulePayload}: $payload'
                              '${missedRunsWarning == null ? '' : '\n$missedRunsWarning'}',
                              maxLines: 6,
                              overflow: TextOverflow.ellipsis,
                            ),
                          ),
                          isThreeLine: true,
                          trailing: Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Semantics(
                                label: l10n.scheduleEnabledFor(
                                  item['name'] as String? ?? '',
                                ),
                                child: Switch(
                                  value: enabled,
                                  onChanged: _togglingIds.contains(item['id'])
                                      ? null
                                      : (value) => _toggle(item, value),
                                ),
                              ),
                              PopupMenuButton<String>(
                                tooltip: l10n.scheduleActions,
                                onSelected: (action) {
                                  if (action == 'edit') _edit(item);
                                  if (action == 'delete') _delete(item);
                                },
                                itemBuilder: (_) => [
                                  PopupMenuItem(value: 'edit', child: Text(l10n.scheduleEdit)),
                                  PopupMenuItem(value: 'delete', child: Text(l10n.scheduleDelete)),
                                ],
                              ),
                            ],
                          ),
                          onTap: () => _edit(item),
                        );
                      },
                    ),
            ),
    );
  }
}

class _ScheduleDraft {
  const _ScheduleDraft({
    required this.name,
    required this.cron,
    required this.timezone,
    required this.payload,
    required this.enabled,
  });

  final String name;
  final String cron;
  final String timezone;
  final Map<String, dynamic> payload;
  final bool enabled;
}

class _ScheduleEditorPage extends StatefulWidget {
  const _ScheduleEditorPage({required this.schedule});
  final Map<String, dynamic>? schedule;

  @override
  State<_ScheduleEditorPage> createState() => _ScheduleEditorPageState();
}

class _ScheduleEditorPageState extends State<_ScheduleEditorPage> {
  final _formKey = GlobalKey<FormState>();
  late final TextEditingController _name;
  late final TextEditingController _cron;
  late final TextEditingController _timezone;
  late final TextEditingController _payload;
  late bool _enabled;
  String? _payloadError;

  @override
  void initState() {
    super.initState();
    final item = widget.schedule;
    _name = TextEditingController(text: item?['name'] as String? ?? '');
    _cron = TextEditingController(text: item?['cron'] as String? ?? '0 9 * * *');
    _timezone = TextEditingController(text: item?['timezone'] as String? ?? 'UTC');
    final payload = item?['payload'] is Map ? item!['payload'] : <String, dynamic>{};
    _payload = TextEditingController(text: const JsonEncoder.withIndent('  ').convert(payload));
    _enabled = item?['enabled'] != false;
  }

  @override
  void dispose() {
    _name.dispose(); _cron.dispose(); _timezone.dispose(); _payload.dispose();
    super.dispose();
  }

  String? _required(String? value) => value == null || value.trim().isEmpty
      ? AppLocalizations.of(context).scheduleRequired
      : null;

  void _save() {
    if (!_formKey.currentState!.validate()) return;
    Map<String, dynamic> payload;
    try {
      final decoded = jsonDecode(_payload.text);
      if (decoded is! Map) throw const FormatException();
      payload = Map<String, dynamic>.from(decoded);
      _payloadError = null;
    } catch (_) {
      setState(() => _payloadError = AppLocalizations.of(context).scheduleInvalidJson);
      return;
    }
    Navigator.of(context).pop(_ScheduleDraft(
      name: _name.text.trim(),
      cron: _cron.text.trim(),
      timezone: _timezone.text.trim(),
      payload: payload,
      enabled: _enabled,
    ));
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return AppScaffold(
      title: Text(widget.schedule == null ? l10n.scheduleNew : l10n.scheduleEditTitle),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: Text(l10n.scheduleCancel),
        ),
        FilledButton(onPressed: _save, child: Text(l10n.scheduleSave)),
      ],
      body: Form(
        key: _formKey,
        child: ListView(
          padding: const EdgeInsets.all(AppSpacing.md),
          children: [
            TextFormField(controller: _name, decoration: InputDecoration(labelText: l10n.scheduleName), validator: _required, maxLength: 200),
            TextFormField(controller: _cron, decoration: InputDecoration(labelText: l10n.scheduleCron, helperText: l10n.scheduleCronHelper), validator: _required, maxLength: 120),
            TextFormField(controller: _timezone, decoration: InputDecoration(labelText: l10n.scheduleTimezone, helperText: l10n.scheduleTimezoneHelper), validator: _required, maxLength: 64),
            TextFormField(controller: _payload, decoration: InputDecoration(labelText: l10n.schedulePayloadJson, errorText: _payloadError), minLines: 4, maxLines: 8, keyboardType: TextInputType.multiline),
            SwitchListTile(contentPadding: EdgeInsets.zero, title: Text(l10n.scheduleEnabled), value: _enabled, onChanged: (value) => setState(() => _enabled = value)),
          ],
        ),
      ),
    );
  }
}
