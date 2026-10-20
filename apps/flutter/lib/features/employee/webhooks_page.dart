import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'package:prodavan/core/session/work_context.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/app_snack_bar.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// События, на которые можно подписаться (совпадают с backend SUPPORTED_EVENTS).
const _webhookEvents = <String, String>{
  'chat.message.completed': 'Агент завершил ответ в чате',
  'chat.turn.failed': 'Ход агента завершился ошибкой',
};

/// Скоупы app-ключа (внешний API).
const _appKeyScopes = <String, String>{
  'chat:read': 'Чтение чатов и сообщений',
  'chat:write': 'Создание чатов, отправка сообщений, импорт тендеров',
  'catalog:read': 'Чтение каталога и позиций',
};

/// «Управление → Вебхуки»: подписки на события + app-токены + документация.
///
/// Источник истины — backend (`/api/v1/external/webhooks`, `/app-keys`):
/// страница только читает и вызывает операции, ничего не кэширует локально.
/// Секреты (secret подписки, токен ключа) показываются РОВНО ОДИН РАЗ —
/// сразу после создания; повторно получить их нельзя, только пересоздать.
class WebhooksPage extends StatefulWidget {
  const WebhooksPage({super.key});

  @override
  State<WebhooksPage> createState() => _WebhooksPageState();
}

class _WebhooksPageState extends State<WebhooksPage>
    with SingleTickerProviderStateMixin {
  late final TabController _tabs = TabController(length: 3, vsync: this);
  bool _loading = true;
  String? _error;
  List<Map<String, dynamic>> _hooks = const [];
  List<Map<String, dynamic>> _keys = const [];

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _tabs.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final api = workContext.api;
      final hooks = await api.listWebhookSubscriptions();
      final keys = await api.listAppKeys();
      if (!mounted) return;
      setState(() {
        _hooks = hooks;
        _keys = keys;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  Future<void> _createWebhook() async {
    final result = await Navigator.of(context).push<_HookDraft>(
      MaterialPageRoute<_HookDraft>(builder: (_) => const _WebhookFormPage()),
    );
    if (result == null || !mounted) return;
    try {
      final created = await workContext.api.createWebhookSubscription(
        url: result.url,
        events: result.events,
        projectId: result.projectId,
      );
      if (!mounted) return;
      await _showSecret(
        title: 'Вебхук создан',
        secret: (created['secret'] ?? '').toString(),
        hint: 'Секрет подписи (X-Prodavan-Signature). Сохраните: он больше не '
            'будет показан. Проверяйте им HMAC-SHA256 от сырого тела запроса.',
      );
      await _load();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  Future<void> _createKey() async {
    final result = await Navigator.of(context).push<_KeyDraft>(
      MaterialPageRoute<_KeyDraft>(builder: (_) => const _AppKeyFormPage()),
    );
    if (result == null || !mounted) return;
    try {
      final created = await workContext.api.createAppKey(
        label: result.label,
        scopes: result.scopes,
      );
      if (!mounted) return;
      await _showSecret(
        title: 'Токен создан',
        secret: (created['token'] ?? '').toString(),
        hint: 'API-токен (Bearer). Сохраните: он показывается один раз. '
            'Передавайте в заголовке Authorization: Bearer <токен>.',
      );
      await _load();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  Future<void> _showSecret({
    required String title,
    required String secret,
    required String hint,
  }) async {
    if (secret.isEmpty) return;
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => _SecretPage(title: title, secret: secret, hint: hint),
      ),
    );
  }

  Future<void> _deleteHook(Map<String, dynamic> hook) async {
    final id = (hook['id'] ?? '').toString();
    if (id.isEmpty) return;
    final ok = await _confirm('Удалить вебхук?', (hook['url'] ?? '').toString());
    if (!ok || !mounted) return;
    try {
      await workContext.api.deleteWebhookSubscription(id);
      await _load();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  Future<void> _revokeKey(Map<String, dynamic> key) async {
    final id = (key['id'] ?? '').toString();
    if (id.isEmpty) return;
    final ok = await _confirm('Отозвать токен?', (key['label'] ?? '').toString());
    if (!ok || !mounted) return;
    try {
      await workContext.api.revokeAppKey(id);
      await _load();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  Future<bool> _confirm(String title, String subtitle) async {
    final res = await Navigator.of(context).push<bool>(
      MaterialPageRoute<bool>(
        builder: (_) => _ConfirmPage(title: title, subtitle: subtitle),
      ),
    );
    return res ?? false;
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return AppScaffold(
      title: const Text('Вебхуки и токены'),
      actions: [
        IconButton(
          tooltip: l10n.commonRetry,
          onPressed: _loading ? null : _load,
          icon: const Icon(Icons.refresh_outlined),
        ),
      ],
      body: Column(
        children: [
          TabBar(
            controller: _tabs,
            tabs: const [
              Tab(text: 'Вебхуки'),
              Tab(text: 'API-токены'),
              Tab(text: 'Документация'),
            ],
          ),
          Expanded(
            child: _loading
                ? const Center(child: CircularProgressIndicator())
                : _error != null
                    ? _ErrorBody(message: _error!, onRetry: _load)
                    : TabBarView(
                        controller: _tabs,
                        children: [
                          _hooksTab(),
                          _keysTab(),
                          const _DocsTab(),
                        ],
                      ),
          ),
        ],
      ),
    );
  }

  Widget _hooksTab() {
    return ListView(
      padding: const EdgeInsets.all(AppSpacing.md),
      children: [
        _SectionHeader(
          title: 'Подписки на события',
          subtitle: 'Backend отправит POST на ваш URL при наступлении события. '
              'Подпись — HMAC-SHA256 в заголовке X-Prodavan-Signature.',
          actionLabel: 'Добавить',
          onAction: _createWebhook,
        ),
        if (_hooks.isEmpty)
          const EmptyPlaceholder(
            title: 'Вебхуков пока нет',
            icon: Icons.webhook_outlined,
          )
        else
          for (final hook in _hooks) _hookTile(hook),
      ],
    );
  }

  Widget _hookTile(Map<String, dynamic> hook) {
    final url = (hook['url'] ?? '').toString();
    final events = (hook['events'] as List?)?.cast<String>() ?? const <String>[];
    final projectId = (hook['project_id'] ?? '').toString();
    final enabled = hook['enabled'] != false;
    final failCount = hook['fail_count'];
    return Card(
      margin: const EdgeInsets.only(bottom: AppSpacing.sm),
      child: ListTile(
        leading: Icon(
          enabled ? Icons.check_circle_outline : Icons.pause_circle_outline,
          color: enabled ? null : Theme.of(context).disabledColor,
        ),
        title: Text(url, maxLines: 1, overflow: TextOverflow.ellipsis),
        subtitle: Text(
          [
            events.map((e) => _webhookEvents[e] ?? e).join(', '),
            if (projectId.isNotEmpty) 'проект: $projectId' else 'все проекты',
            if (failCount is int && failCount > 0) 'ошибок: $failCount',
            if (!enabled) 'выключен',
          ].join(' · '),
        ),
        trailing: IconButton(
          tooltip: 'Удалить',
          icon: const Icon(Icons.delete_outline),
          onPressed: () => _deleteHook(hook),
        ),
      ),
    );
  }

  Widget _keysTab() {
    return ListView(
      padding: const EdgeInsets.all(AppSpacing.md),
      children: [
        _SectionHeader(
          title: 'API-токены',
          subtitle: 'Bearer-токен для внешнего API (интеграции, кравлеры). '
              'Токен показывается один раз при создании.',
          actionLabel: 'Создать',
          onAction: _createKey,
        ),
        if (_keys.isEmpty)
          const EmptyPlaceholder(
            title: 'Токенов пока нет',
            icon: Icons.key_outlined,
          )
        else
          for (final key in _keys) _keyTile(key),
      ],
    );
  }

  Widget _keyTile(Map<String, dynamic> key) {
    final label = (key['label'] ?? '').toString();
    final prefix = (key['token_prefix'] ?? '').toString();
    final scopes = (key['scopes'] as List?)?.cast<String>() ?? const <String>[];
    final status = (key['status'] ?? 'active').toString();
    final revoked = status != 'active';
    return Card(
      margin: const EdgeInsets.only(bottom: AppSpacing.sm),
      child: ListTile(
        leading: Icon(
          revoked ? Icons.block : Icons.vpn_key_outlined,
          color: revoked ? Theme.of(context).disabledColor : null,
        ),
        title: Text(label.isEmpty ? '(без названия)' : label),
        subtitle: Text(
          [
            if (prefix.isNotEmpty) '$prefix…',
            scopes.join(', '),
            if (revoked) 'отозван',
          ].join(' · '),
        ),
        trailing: revoked
            ? null
            : IconButton(
                tooltip: 'Отозвать',
                icon: const Icon(Icons.delete_outline),
                onPressed: () => _revokeKey(key),
              ),
      ),
    );
  }
}

// ------------------------------------------------------------------ dialogs

class _HookDraft {
  const _HookDraft(this.url, this.events, this.projectId);
  final String url;
  final List<String> events;
  final String? projectId;
}

class _WebhookFormPage extends StatefulWidget {
  const _WebhookFormPage();

  @override
  State<_WebhookFormPage> createState() => _WebhookFormPageState();
}

class _WebhookFormPageState extends State<_WebhookFormPage> {
  final _url = TextEditingController();
  final _project = TextEditingController();
  final _selected = <String>{'chat.message.completed'};

  @override
  void dispose() {
    _url.dispose();
    _project.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final valid = _url.text.trim().startsWith('http') && _selected.isNotEmpty;
    return AppScaffold(
      title: const Text('Новый вебхук'),
      body: ListView(
        padding: const EdgeInsets.all(AppSpacing.md),
        children: [
          TextField(
            controller: _url,
            autofocus: true,
            decoration: const InputDecoration(
              labelText: 'URL приёмника',
              hintText: 'https://example.com/hooks/agentscale',
            ),
            onChanged: (_) => setState(() {}),
          ),
          const SizedBox(height: AppSpacing.sm),
          TextField(
            controller: _project,
            decoration: const InputDecoration(
              labelText: 'ID проекта (необязательно)',
              helperText: 'Пусто — события всех проектов компании',
            ),
          ),
          const SizedBox(height: AppSpacing.md),
          const Text('События'),
          for (final entry in _webhookEvents.entries)
            CheckboxListTile(
              dense: true,
              contentPadding: EdgeInsets.zero,
              controlAffinity: ListTileControlAffinity.leading,
              title: Text(entry.value),
              subtitle: Text(entry.key),
              value: _selected.contains(entry.key),
              onChanged: (on) => setState(() {
                if (on == true) {
                  _selected.add(entry.key);
                } else {
                  _selected.remove(entry.key);
                }
              }),
            ),
          const SizedBox(height: AppSpacing.lg),
          FilledButton(
            onPressed: valid
                ? () => Navigator.of(context).pop(
                      _HookDraft(
                        _url.text.trim(),
                        _selected.toList(),
                        _project.text.trim().isEmpty ? null : _project.text.trim(),
                      ),
                    )
                : null,
            child: const Text('Создать'),
          ),
        ],
      ),
    );
  }
}

class _KeyDraft {
  const _KeyDraft(this.label, this.scopes);
  final String label;
  final List<String> scopes;
}

class _AppKeyFormPage extends StatefulWidget {
  const _AppKeyFormPage();

  @override
  State<_AppKeyFormPage> createState() => _AppKeyFormPageState();
}

class _AppKeyFormPageState extends State<_AppKeyFormPage> {
  final _label = TextEditingController();
  final _selected = <String>{'chat:read', 'chat:write'};

  @override
  void dispose() {
    _label.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final valid = _label.text.trim().isNotEmpty && _selected.isNotEmpty;
    return AppScaffold(
      title: const Text('Новый API-токен'),
      body: ListView(
        padding: const EdgeInsets.all(AppSpacing.md),
        children: [
          TextField(
            controller: _label,
            autofocus: true,
            decoration: const InputDecoration(
              labelText: 'Название',
              hintText: 'например, b2b-crawler',
            ),
            onChanged: (_) => setState(() {}),
          ),
          const SizedBox(height: AppSpacing.md),
          const Text('Права'),
          for (final entry in _appKeyScopes.entries)
            CheckboxListTile(
              dense: true,
              contentPadding: EdgeInsets.zero,
              controlAffinity: ListTileControlAffinity.leading,
              title: Text(entry.key),
              subtitle: Text(entry.value),
              value: _selected.contains(entry.key),
              onChanged: (on) => setState(() {
                if (on == true) {
                  _selected.add(entry.key);
                } else {
                  _selected.remove(entry.key);
                }
              }),
            ),
          const SizedBox(height: AppSpacing.lg),
          FilledButton(
            onPressed: valid
                ? () => Navigator.of(context).pop(
                      _KeyDraft(_label.text.trim(), _selected.toList()),
                    )
                : null,
            child: const Text('Создать'),
          ),
        ],
      ),
    );
  }
}

/// Одноразовый показ секрета (секрет подписки / API-токен).
class _SecretPage extends StatelessWidget {
  const _SecretPage({
    required this.title,
    required this.secret,
    required this.hint,
  });

  final String title;
  final String secret;
  final String hint;

  @override
  Widget build(BuildContext context) {
    return AppScaffold(
      title: Text(title),
      body: ListView(
        padding: const EdgeInsets.all(AppSpacing.md),
        children: [
          SelectableText(
            secret,
            style: const TextStyle(fontFamily: 'monospace', fontSize: 13),
          ),
          const SizedBox(height: AppSpacing.sm),
          Text(hint, style: Theme.of(context).textTheme.bodySmall),
          const SizedBox(height: AppSpacing.lg),
          FilledButton.tonalIcon(
            onPressed: () async {
              await Clipboard.setData(ClipboardData(text: secret));
              if (context.mounted) {
                AppSnackBar.success(context, 'Скопировано');
              }
            },
            icon: const Icon(Icons.copy_outlined, size: 18),
            label: const Text('Скопировать'),
          ),
          const SizedBox(height: AppSpacing.sm),
          OutlinedButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('Готово'),
          ),
        ],
      ),
    );
  }
}

/// Страница подтверждения удаления/отзыва.
class _ConfirmPage extends StatelessWidget {
  const _ConfirmPage({required this.title, required this.subtitle});

  final String title;
  final String subtitle;

  @override
  Widget build(BuildContext context) {
    return AppScaffold(
      title: Text(title),
      body: ListView(
        padding: const EdgeInsets.all(AppSpacing.md),
        children: [
          Text(subtitle),
          const SizedBox(height: AppSpacing.lg),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('Удалить'),
          ),
          const SizedBox(height: AppSpacing.sm),
          OutlinedButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Отмена'),
          ),
        ],
      ),
    );
  }
}

// --------------------------------------------------------------- docs tab

class _DocsTab extends StatelessWidget {
  const _DocsTab();

  @override
  Widget build(BuildContext context) {
    final mono = Theme.of(context).textTheme.bodySmall?.copyWith(
          fontFamily: 'monospace',
        );
    return ListView(
      padding: const EdgeInsets.all(AppSpacing.md),
      children: [
        const _DocHeading('1. Получить токен'),
        const _DocText(
          'На вкладке «API-токены» создайте токен с нужными правами. Он '
          'показывается один раз — сохраните его. Передавайте в каждом запросе:',
        ),
        SelectableText('Authorization: Bearer ak_...', style: mono),
        const _DocHeading('2. Импорт тендера'),
        const _DocText(
          'POST /api/v1/external/projects/{project_id}/tenders. Обязателен '
          'source_ref — он делает импорт идемпотентным: повторный POST с тем же '
          'source_ref обновляет тендер, а не создаёт дубль.',
        ),
        SelectableText(
          '{\n'
          '  "title": "Поставка оборудования",\n'
          '  "customer": "АО Заказчик",\n'
          '  "deadline_at": "2026-12-31T18:00:00+00:00",\n'
          '  "source_ref": "b2b-center:123456",\n'
          '  "source_url": "https://...",\n'
          '  "lines": [{"id": "p1", "title": "Сервер", "qty": 2}],\n'
          '  "attach": true,\n'
          '  "kickoff": true\n'
          '}',
          style: mono,
        ),
        const _DocText(
          'attach=true — создать чат и импортировать позиции; kickoff=true — '
          'сразу запустить агента на разбор тендера (оценка релевантности и '
          'подбор кандидатов по позициям).',
        ),
        const _DocHeading('3. Вебхук: получить ответ агента'),
        const _DocText(
          'Создайте подписку на chat.message.completed. Backend отправит POST:',
        ),
        SelectableText(
          '{\n'
          '  "id": "whd_...",\n'
          '  "type": "chat.message.completed",\n'
          '  "created_at": "...",\n'
          '  "data": { "source_ref": "b2b-center:123456", ... }\n'
          '}',
          style: mono,
        ),
        const _DocText(
          'Заголовки: X-Prodavan-Signature (sha256=<hex>), X-Prodavan-Event, '
          'X-Prodavan-Delivery. Подпись — HMAC-SHA256 от СЫРОГО тела с секретом '
          'подписки. Отвечайте 2xx; при ошибке доставка повторяется с бэкоффом.',
        ),
        const _DocHeading('4. Проверка подписи (Python)'),
        SelectableText(
          'import hmac, hashlib\n'
          'expected = "sha256=" + hmac.new(\n'
          '    secret.encode(), raw_body, hashlib.sha256\n'
          ').hexdigest()\n'
          'assert hmac.compare_digest(\n'
          '    expected, request.headers["X-Prodavan-Signature"])',
          style: mono,
        ),
        const _DocHeading('Рекомендации'),
        const _DocText(
          '• Отвечайте 2xx быстро, обработку делайте асинхронно.\n'
          '• Дедуплицируйте по X-Prodavan-Delivery — доставка может повториться.\n'
          '• Не логируйте секрет подписи и токен.\n'
          '• Токен можно отозвать в любой момент на вкладке «API-токены».',
        ),
      ],
    );
  }
}

class _DocHeading extends StatelessWidget {
  const _DocHeading(this.text);
  final String text;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: AppSpacing.md, bottom: AppSpacing.xs),
      child: Text(text, style: Theme.of(context).textTheme.titleSmall),
    );
  }
}

class _DocText extends StatelessWidget {
  const _DocText(this.text);
  final String text;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: AppSpacing.xs),
      child: Text(text, style: Theme.of(context).textTheme.bodySmall),
    );
  }
}

// ------------------------------------------------------------------ helpers

class _SectionHeader extends StatelessWidget {
  const _SectionHeader({
    required this.title,
    required this.subtitle,
    required this.actionLabel,
    required this.onAction,
  });

  final String title;
  final String subtitle;
  final String actionLabel;
  final VoidCallback onAction;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: AppSpacing.md),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(title, style: Theme.of(context).textTheme.titleMedium),
              ),
              FilledButton.tonalIcon(
                onPressed: onAction,
                icon: const Icon(Icons.add, size: 18),
                label: Text(actionLabel),
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.xs),
          Text(subtitle, style: Theme.of(context).textTheme.bodySmall),
        ],
      ),
    );
  }
}

class _ErrorBody extends StatelessWidget {
  const _ErrorBody({required this.message, required this.onRetry});
  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.md),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(message, textAlign: TextAlign.center),
            const SizedBox(height: AppSpacing.md),
            FilledButton(onPressed: onRetry, child: const Text('Повторить')),
          ],
        ),
      ),
    );
  }
}
