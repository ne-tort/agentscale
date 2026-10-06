import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/utils/open_external_url.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Состояние device-code потока (ответы сервера):
/// status ∈ none|pending|authorized|denied|expired|error.
typedef GrokDeviceState = Map<String, dynamic>;

/// Авторизация Grok (xAI) — device-code flow (RFC 8628).
///
/// Сервер выдаёт ссылку и код подтверждения; пользователь открывает ссылку
/// в браузере (вход в аккаунт SuperGrok), а страница поллит статус, пока
/// сервер обменивает device_code на токены. Токены остаются на сервере и
/// обновляются автоматически — в чате ключ работает как обычный провайдер.
///
/// Колбэки инъектируются (company/admin контуры дают свои API-вызовы) —
/// страницу можно тестировать с фейками.
class AiKeyGrokOauthPage extends StatefulWidget {
  const AiKeyGrokOauthPage({
    super.key,
    required this.start,
    required this.status,
    this.pollInterval = const Duration(seconds: 4),
  });

  final Future<GrokDeviceState> Function() start;
  final Future<GrokDeviceState> Function() status;
  final Duration pollInterval;

  @override
  State<AiKeyGrokOauthPage> createState() => _AiKeyGrokOauthPageState();
}

class _AiKeyGrokOauthPageState extends State<AiKeyGrokOauthPage> {
  GrokDeviceState? _state;
  Object? _error;
  bool _busy = true;
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    _restart();
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  String get _status => _state?['status']?.toString() ?? 'none';

  bool get _terminal =>
      _status == 'authorized' || _status == 'denied' || _status == 'expired' || _status == 'error';

  Future<void> _restart() async {
    _timer?.cancel();
    setState(() {
      _busy = true;
      _error = null;
      _state = null;
    });
    try {
      final state = await widget.start();
      if (!mounted) return;
      setState(() => _state = state);
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = e);
    }
    if (!mounted) return;
    setState(() => _busy = false);
    _schedulePoll();
  }

  void _schedulePoll() {
    _timer?.cancel();
    if (_busy || _error != null || _terminal) return;
    _timer = Timer(widget.pollInterval, _poll);
  }

  Future<void> _poll() async {
    try {
      final state = await widget.status();
      if (!mounted) return;
      setState(() => _state = state);
      if (_status == 'authorized') {
        Navigator.of(context).pop(true);
        return;
      }
    } catch (_) {
      // транзиентная ошибка статуса — поллим дальше
    }
    if (!mounted) return;
    _schedulePoll();
  }

  Future<void> _openLink() async {
    final url = (_state?['verification_uri_complete'] ?? _state?['verification_uri'])?.toString() ?? '';
    if (url.isEmpty) return;
    final ok = await openExternalUrl(url);
    if (!ok && mounted) {
      // не смогли открыть автоматически — даём скопировать ссылку
      await Clipboard.setData(ClipboardData(text: url));
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('🔗 URL → clipboard')),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final colors = context.appColors;
    return AppScaffold(
      title: Text(l10n.aiKeyGrokAuthTitle),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 460),
          child: Padding(
            padding: const EdgeInsets.all(AppSpacing.md),
            child: _busy
                ? const CircularProgressIndicator()
                : _error != null
                    ? _failureCard(
                        l10n,
                        colors,
                        AppErrors.present(_error!, l10n).display,
                      )
                    : _body(l10n, colors),
          ),
        ),
      ),
    );
  }

  Widget _body(AppLocalizations l10n, AppColorTokens colors) {
    switch (_status) {
      case 'pending':
        return _pendingCard(l10n, colors);
      case 'denied':
        return _failureCard(l10n, colors, l10n.aiKeyGrokAuthDenied);
      case 'expired':
        return _failureCard(l10n, colors, l10n.aiKeyGrokAuthExpired);
      case 'error':
        final detail = _state?['error']?.toString() ?? '';
        return _failureCard(
          l10n,
          colors,
          detail.isEmpty ? l10n.aiKeyGrokAuthError : '${l10n.aiKeyGrokAuthError}: $detail',
        );
      case 'authorized':
        // pop происходит в _poll; краткий успех, если всё же отрисовалось
        return _successCard(l10n, colors);
      default:
        return _failureCard(l10n, colors, l10n.aiKeyGrokAuthExpired);
    }
  }

  Widget _pendingCard(AppLocalizations l10n, AppColorTokens colors) {
    final userCode = _state?['user_code']?.toString() ?? '';
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.lg),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              l10n.aiKeyGrokAuthHint,
              style: Theme.of(context).textTheme.bodyMedium?.copyWith(color: colors.muted),
            ),
            const SizedBox(height: AppSpacing.md),
            if (userCode.isNotEmpty) ...[
              Text(
                l10n.aiKeyGrokAuthCode,
                style: Theme.of(context).textTheme.labelMedium?.copyWith(color: colors.muted),
              ),
              const SizedBox(height: AppSpacing.xs),
              Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  SelectableText(
                    userCode,
                    style: Theme.of(context).textTheme.headlineSmall?.copyWith(
                          fontWeight: FontWeight.w700,
                          letterSpacing: 2,
                          fontFeatures: const [FontFeature.tabularFigures()],
                        ),
                  ),
                  IconButton(
                    tooltip: l10n.aiKeyGrokAuthCode,
                    icon: const Icon(Icons.copy_rounded, size: 18),
                    onPressed: () => Clipboard.setData(ClipboardData(text: userCode)),
                  ),
                ],
              ),
              const SizedBox(height: AppSpacing.md),
            ],
            FilledButton.icon(
              onPressed: _openLink,
              icon: const Icon(Icons.open_in_new_rounded),
              label: Text(l10n.aiKeyGrokAuthOpenLink),
            ),
            const SizedBox(height: AppSpacing.md),
            Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                const SizedBox(
                  width: 14,
                  height: 14,
                  child: CircularProgressIndicator(strokeWidth: 2),
                ),
                const SizedBox(width: AppSpacing.sm),
                Text(
                  l10n.aiKeyGrokAuthWaiting,
                  style: Theme.of(context).textTheme.bodySmall?.copyWith(color: colors.muted),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _successCard(AppLocalizations l10n, AppColorTokens colors) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.lg),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.check_circle_outline_rounded, color: colors.success, size: 40),
            const SizedBox(height: AppSpacing.sm),
            Text(l10n.aiKeyGrokAuthDone),
          ],
        ),
      ),
    );
  }

  Widget _failureCard(AppLocalizations l10n, AppColorTokens colors, String message) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.lg),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                Icon(Icons.error_outline_rounded, color: colors.danger, size: 28),
                const SizedBox(width: AppSpacing.sm),
                Expanded(
                  child: Text(
                    message,
                    style: Theme.of(context).textTheme.bodyMedium,
                  ),
                ),
              ],
            ),
            const SizedBox(height: AppSpacing.md),
            OutlinedButton.icon(
              onPressed: _restart,
              icon: const Icon(Icons.refresh_rounded),
              label: Text(l10n.aiKeyGrokAuthRestart),
            ),
          ],
        ),
      ),
    );
  }
}
