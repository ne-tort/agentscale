import 'package:flutter/material.dart';

import 'package:prodavan/core/preferences/preferences.dart';
import 'package:prodavan/core/session/admin_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_catalog_select_page.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/features/admin/ai_key_integration_type.dart';
import 'package:prodavan/l10n/app_localizations.dart';

const kAiHttpProvidersCatalogId = 'ai.http_providers';

/// Canonical payload keys for `ai.http_providers` (Clowbot / OpenAPI-compatible).
abstract final class AiHttpProviderPayload {
  static const apiKind = 'api_kind';
  static const agentProvider = 'agent_provider';
  static const baseUrl = 'base_url';
  static const openaiCompatible = 'openai_compatible';
  static const authScheme = 'auth_scheme';
  static const chatCompletionsPath = 'chat_completions_path';
  static const modelsPath = 'models_path';

  static const apiKinds = [
    'openai_api',
    'anthropic_api',
    'openrouter',
    'custom',
  ];

  static const agentProviders = ['codex', 'claude_code', 'cursor'];
  static const authSchemes = ['bearer', 'x-api-key', 'none'];
}

/// Editable catalog picker for AI HTTP providers (`ai.http_providers`).
class AiHttpProviderSelectPage extends StatefulWidget {
  const AiHttpProviderSelectPage({
    super.key,
    this.selectedEntryId,
    this.selectedApiKind,
    this.selectedProvider,
  });

  final String? selectedEntryId;
  final String? selectedApiKind;
  final String? selectedProvider;

  static Future<AppCatalogSelectItem?> push(
    BuildContext context, {
    String? selectedEntryId,
    String? selectedApiKind,
    String? selectedProvider,
  }) async {
    final ids = await Navigator.of(context).push<Set<String>>(
      MaterialPageRoute(
        builder: (_) => AiHttpProviderSelectPage(
          selectedEntryId: selectedEntryId,
          selectedApiKind: selectedApiKind,
          selectedProvider: selectedProvider,
        ),
      ),
    );
    if (ids == null || ids.isEmpty) return null;
    final rows =
        await adminContext.api.listCatalogEntries(kAiHttpProvidersCatalogId);
    for (final r in rows) {
      if (r['id'] == ids.first) {
        return AppCatalogSelectItem(
          id: r['id'] as String,
          title: r['title'] as String? ?? r['id'] as String,
          subtitle: r['subtitle'] as String?,
          icon: catalogIconForName(r['icon_name'] as String?),
          payload: {
            ...((r['payload'] as Map?)?.cast<String, dynamic>() ?? const {}),
            'title': r['title'],
          },
        );
      }
    }
    return null;
  }

  @override
  State<AiHttpProviderSelectPage> createState() =>
      _AiHttpProviderSelectPageState();
}

class _AiHttpProviderSelectPageState extends State<AiHttpProviderSelectPage> {
  bool _loading = true;
  List<AppCatalogSelectItem> _items = const [];

  @override
  void initState() {
    super.initState();
    _reload();
  }

  Future<void> _reload() async {
    setState(() => _loading = true);
    try {
      final rows =
          await adminContext.api.listCatalogEntries(kAiHttpProvidersCatalogId);
      if (!mounted) return;
      setState(() {
        _items = [
          for (final r in rows)
            AppCatalogSelectItem(
              id: r['id'] as String,
              title: r['title'] as String? ?? r['id'] as String,
              subtitle: _subtitleFor(r),
              icon: catalogIconForName(r['icon_name'] as String?),
              payload: {
                ...((r['payload'] as Map?)?.cast<String, dynamic>() ??
                    const {}),
              },
            ),
        ];
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      AppErrors.showSnack(context, e);
    }
  }

  String? _subtitleFor(Map<String, dynamic> r) {
    final payload = (r['payload'] as Map?)?.cast<String, dynamic>() ?? const {};
    final base = payload[AiHttpProviderPayload.baseUrl]?.toString().trim();
    if (base != null && base.isNotEmpty) return base;
    return r['subtitle'] as String?;
  }

  String? get _selectedId {
    if (widget.selectedEntryId != null) return widget.selectedEntryId;
    final apiKind = widget.selectedApiKind;
    final provider = widget.selectedProvider;
    if (apiKind == null) return null;
    for (final item in _items) {
      final p = item.payload;
      if (p[AiHttpProviderPayload.apiKind] == apiKind &&
          (provider == null ||
              p[AiHttpProviderPayload.agentProvider] == provider)) {
        return item.id;
      }
    }
    for (final item in _items) {
      if (item.payload[AiHttpProviderPayload.apiKind] == apiKind) {
        return item.id;
      }
    }
    return null;
  }

  Future<void> _create(String name) async {
    final created = await adminContext.api.createCatalogEntry(
      catalogId: kAiHttpProvidersCatalogId,
      title: name.trim(),
      subtitle: null,
      payload: const {
        AiHttpProviderPayload.apiKind: 'custom',
        AiHttpProviderPayload.agentProvider: 'codex',
        AiHttpProviderPayload.baseUrl: '',
        AiHttpProviderPayload.openaiCompatible: true,
        AiHttpProviderPayload.authScheme: 'bearer',
        AiHttpProviderPayload.chatCompletionsPath: '/v1/chat/completions',
        AiHttpProviderPayload.modelsPath: '/v1/models',
      },
    );
    await _reload();
    if (!mounted) return;
    final item = AppCatalogSelectItem(
      id: created['id'] as String,
      title: created['title'] as String? ?? name.trim(),
      subtitle: created['subtitle'] as String?,
      payload: {
        ...((created['payload'] as Map?)?.cast<String, dynamic>() ?? const {}),
      },
    );
    await Navigator.of(context).push<void>(
      MaterialPageRoute(
        builder: (_) => AiHttpProviderEditPage(item: item),
      ),
    );
    await _reload();
  }

  Future<void> _edit(AppCatalogSelectItem item) async {
    await Navigator.of(context).push<void>(
      MaterialPageRoute(
        builder: (_) => AiHttpProviderEditPage(item: item),
      ),
    );
    await _reload();
  }

  Future<void> _delete(AppCatalogSelectItem item) async {
    await adminContext.api.deleteCatalogEntry(
      catalogId: kAiHttpProvidersCatalogId,
      entryId: item.id,
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    if (_loading && _items.isEmpty) {
      return AppScaffold(
        title: Text(l10n.commonProvider),
        body: const Center(child: CircularProgressIndicator()),
      );
    }

    final selected = _selectedId;
    return AppCatalogSelectPage(
      title: l10n.commonProvider,
      items: _items,
      selectedIds: selected == null ? const {} : {selected},
      popOnSelect: true,
      allowCreate: true,
      allowEdit: true,
      allowDelete: true,
      addFieldTitle: l10n.adminAddHttpProvider,
      onCreate: _create,
      onEdit: _edit,
      onDelete: _delete,
    );
  }
}

/// Preference editor for one HTTP provider catalog entry (immediate saves).
class AiHttpProviderEditPage extends StatefulWidget {
  const AiHttpProviderEditPage({super.key, required this.item});

  final AppCatalogSelectItem item;

  @override
  State<AiHttpProviderEditPage> createState() => _AiHttpProviderEditPageState();
}

class _AiHttpProviderEditPageState extends State<AiHttpProviderEditPage> {
  late String _title;
  late String _baseUrl;
  late String _apiKind;
  late String _agentProvider;
  late bool _openaiCompatible;
  late String _authScheme;
  late String _chatPath;
  late String _modelsPath;

  @override
  void initState() {
    super.initState();
    final p = widget.item.payload;
    _title = widget.item.title;
    _baseUrl = p[AiHttpProviderPayload.baseUrl]?.toString() ?? '';
    _apiKind = p[AiHttpProviderPayload.apiKind]?.toString() ?? 'custom';
    if (!AiHttpProviderPayload.apiKinds.contains(_apiKind)) {
      _apiKind = 'custom';
    }
    _agentProvider =
        p[AiHttpProviderPayload.agentProvider]?.toString() ?? 'codex';
    if (!AiHttpProviderPayload.agentProviders.contains(_agentProvider)) {
      _agentProvider = 'codex';
    }
    _openaiCompatible = p[AiHttpProviderPayload.openaiCompatible] != false;
    _authScheme = p[AiHttpProviderPayload.authScheme]?.toString() ?? 'bearer';
    if (!AiHttpProviderPayload.authSchemes.contains(_authScheme)) {
      _authScheme = 'bearer';
    }
    _chatPath = p[AiHttpProviderPayload.chatCompletionsPath]?.toString() ??
        '/v1/chat/completions';
    _modelsPath =
        p[AiHttpProviderPayload.modelsPath]?.toString() ?? '/v1/models';
  }

  Map<String, dynamic> get _payload => {
        AiHttpProviderPayload.apiKind: _apiKind,
        AiHttpProviderPayload.agentProvider: _agentProvider,
        AiHttpProviderPayload.baseUrl: _baseUrl.trim(),
        AiHttpProviderPayload.openaiCompatible: _openaiCompatible,
        AiHttpProviderPayload.authScheme: _authScheme,
        AiHttpProviderPayload.chatCompletionsPath: _chatPath.trim(),
        AiHttpProviderPayload.modelsPath: _modelsPath.trim(),
      };

  String? _hostSubtitle() {
    final raw = _baseUrl.trim();
    if (raw.isEmpty) return null;
    try {
      final uri = Uri.parse(raw);
      return uri.host.isEmpty ? raw : uri.host;
    } catch (_) {
      return raw;
    }
  }

  Future<void> _patch({String? title}) async {
    await adminContext.api.patchCatalogEntry(
      catalogId: kAiHttpProvidersCatalogId,
      entryId: widget.item.id,
      title: title ?? _title,
      subtitle: _hostSubtitle(),
      payload: _payload,
    );
  }

  String _apiKindLabel(AppLocalizations l10n, String kind) => switch (kind) {
        'openai_api' => l10n.adminHttpApiKindOpenai,
        'anthropic_api' => l10n.adminHttpApiKindAnthropic,
        'openrouter' => l10n.adminHttpApiKindOpenrouter,
        _ => l10n.adminHttpApiKindCustom,
      };

  String _agentLabel(AppLocalizations l10n, String v) => switch (v) {
        'cursor' => l10n.adminTypeCursorSdk,
        'claude_code' => l10n.adminTypeClaudeSdk,
        _ => l10n.adminTypeCodexSdk,
      };

  String _authLabel(AppLocalizations l10n, String v) => switch (v) {
        'x-api-key' => l10n.adminHttpAuthXApiKey,
        'none' => l10n.adminHttpAuthNone,
        _ => l10n.adminHttpAuthBearer,
      };

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return AppScaffold(
      title: Text(l10n.commonProvider),
      body: ListView(
        padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
        children: [
          AppValuePreference<String>(
            title: l10n.commonName,
            icon: Icons.label_outline_rounded,
            value: _title,
            onSave: (v) async {
              final next = v.trim();
              if (next.isEmpty) return;
              _title = next;
              await _patch(title: next);
              setState(() {});
            },
          ),
          AppValuePreference<String>(
            title: l10n.adminHttpBaseUrl,
            icon: Icons.link_rounded,
            value: _baseUrl,
            hintText: 'https://api.openai.com/v1',
            keyboardType: TextInputType.url,
            presentValue: (v) =>
                v.trim().isEmpty ? l10n.commonNotSet : v.trim(),
            onSave: (v) async {
              _baseUrl = v.trim();
              await _patch();
              setState(() {});
            },
          ),
          AppChoicePreference<String>(
            title: l10n.adminHttpApiKind,
            icon: Icons.api_rounded,
            value: _apiKind,
            choices: AiHttpProviderPayload.apiKinds,
            keyFor: (v) => v,
            labelFor: (v) => _apiKindLabel(l10n, v),
            onSave: (v) async {
              _apiKind = v;
              if (v == 'openai_api' || v == 'openrouter' || v == 'custom') {
                _openaiCompatible = true;
              } else if (v == 'anthropic_api') {
                _openaiCompatible = false;
              }
              await _patch();
              setState(() {});
            },
          ),
          AppChoicePreference<String>(
            title: l10n.adminPreferredProvider,
            icon: Icons.smart_toy_outlined,
            value: _agentProvider,
            choices: AiHttpProviderPayload.agentProviders,
            keyFor: (v) => v,
            labelFor: (v) => _agentLabel(l10n, v),
            onSave: (v) async {
              _agentProvider = v;
              await _patch();
              setState(() {});
            },
          ),
          AppSwitchPreference(
            title: l10n.adminHttpOpenaiCompatible,
            icon: Icons.sync_alt_rounded,
            value: _openaiCompatible,
            onChanged: (v) async {
              _openaiCompatible = v;
              await _patch();
              setState(() {});
            },
          ),
          AppChoicePreference<String>(
            title: l10n.adminHttpAuthScheme,
            icon: Icons.key_outlined,
            value: _authScheme,
            choices: AiHttpProviderPayload.authSchemes,
            keyFor: (v) => v,
            labelFor: (v) => _authLabel(l10n, v),
            onSave: (v) async {
              _authScheme = v;
              await _patch();
              setState(() {});
            },
          ),
          AppValuePreference<String>(
            title: l10n.adminHttpChatPath,
            icon: Icons.chat_outlined,
            value: _chatPath,
            presentValue: (v) =>
                v.trim().isEmpty ? l10n.commonNotSet : v.trim(),
            onSave: (v) async {
              _chatPath = v.trim().isEmpty ? '/v1/chat/completions' : v.trim();
              await _patch();
              setState(() {});
            },
          ),
          AppValuePreference<String>(
            title: l10n.adminHttpModelsPath,
            icon: Icons.list_alt_rounded,
            value: _modelsPath,
            presentValue: (v) =>
                v.trim().isEmpty ? l10n.commonNotSet : v.trim(),
            onSave: (v) async {
              _modelsPath = v.trim().isEmpty ? '/v1/models' : v.trim();
              await _patch();
              setState(() {});
            },
          ),
        ],
      ),
    );
  }
}
