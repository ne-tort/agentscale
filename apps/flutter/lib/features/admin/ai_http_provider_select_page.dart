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

/// Canonical payload keys for `ai.http_providers` (Platform OpenClaw / OpenAPI-compatible).
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

  static const authSchemes = ['bearer', 'x-api-key', 'none'];

  static String agentProviderForApiKind(String apiKind) => switch (apiKind) {
        'anthropic_api' => 'claude_code',
        'cursor' => 'cursor',
        _ => 'codex',
      };

  static Map<String, dynamic> defaultsForCustom() => {
        apiKind: 'custom',
        agentProvider: 'codex',
        baseUrl: '',
        openaiCompatible: true,
        authScheme: 'bearer',
        chatCompletionsPath: '/v1/chat/completions',
        modelsPath: '/v1/models',
      };
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
            'seeded': r['seeded'] == true,
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
                'seeded': r['seeded'] == true,
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
      payload: AiHttpProviderPayload.defaultsForCustom(),
    );
    await _reload();
    if (!mounted) return;
    final item = AppCatalogSelectItem(
      id: created['id'] as String,
      title: created['title'] as String? ?? name.trim(),
      subtitle: created['subtitle'] as String?,
      payload: {
        ...((created['payload'] as Map?)?.cast<String, dynamic>() ?? const {}),
        'seeded': created['seeded'] == true,
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
        title: Text(l10n.adminHttpEndpoint),
        body: const Center(child: CircularProgressIndicator()),
      );
    }

    final selected = _selectedId;
    return AppCatalogSelectPage(
      title: l10n.adminHttpEndpoint,
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
///
/// Seeded presets: name only (endpoints known). Custom OpenAPI-compatible:
/// base URL + auth + paths. No OpenAI-compat checkbox (always true for custom).
/// `agent_provider` is derived from api_kind for resolve — not shown in UI.
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
  late String _authScheme;
  late String _chatPath;
  late String _modelsPath;

  /// OpenAPI-compatible custom endpoints (incl. seeded Ollama/Cursor) expose
  /// base URL / auth / paths. Known cloud presets only allow renaming.
  bool get _isCustomEndpoint => _apiKind == 'custom';

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
    _authScheme = p[AiHttpProviderPayload.authScheme]?.toString() ?? 'bearer';
    if (!AiHttpProviderPayload.authSchemes.contains(_authScheme)) {
      _authScheme = 'bearer';
    }
    _chatPath = p[AiHttpProviderPayload.chatCompletionsPath]?.toString() ??
        '/v1/chat/completions';
    _modelsPath =
        p[AiHttpProviderPayload.modelsPath]?.toString() ?? '/v1/models';
  }

  String get _resolvedAgentProvider {
    if (widget.item.id == 'cursor') return 'cursor';
    return AiHttpProviderPayload.agentProviderForApiKind(_apiKind);
  }

  Map<String, dynamic> get _payload {
    final openaiCompat = _apiKind != 'anthropic_api';
    return {
      AiHttpProviderPayload.apiKind: _apiKind,
      AiHttpProviderPayload.agentProvider: _resolvedAgentProvider,
      AiHttpProviderPayload.baseUrl: _baseUrl.trim(),
      AiHttpProviderPayload.openaiCompatible: openaiCompat,
      AiHttpProviderPayload.authScheme: _authScheme,
      AiHttpProviderPayload.chatCompletionsPath: _chatPath.trim(),
      AiHttpProviderPayload.modelsPath: _modelsPath.trim(),
    };
  }

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

  String _authLabel(AppLocalizations l10n, String v) => switch (v) {
        'x-api-key' => l10n.adminHttpAuthXApiKey,
        'none' => l10n.adminHttpAuthNone,
        _ => l10n.adminHttpAuthBearer,
      };

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return AppScaffold(
      title: Text(l10n.adminHttpEndpoint),
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
          if (_isCustomEndpoint) ...[
            AppValuePreference<String>(
              title: l10n.adminHttpBaseUrl,
              icon: Icons.link_rounded,
              value: _baseUrl,
              hintText: 'http://127.0.0.1:11434/v1',
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
                _chatPath =
                    v.trim().isEmpty ? '/v1/chat/completions' : v.trim();
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
        ],
      ),
    );
  }
}
