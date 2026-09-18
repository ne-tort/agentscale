import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_entity_collection.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/core/widgets/app_scaffold.dart';
import 'package:prodavan/core/widgets/empty_placeholder.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Per-model probe state for one row.
class _ModelProbeState {
  _ModelProbeState();

  bool probing = false;
  Map<String, dynamic>? result;
}

/// AI key models table page — lists models returned by the key probe and lets
/// the user verify each model individually (1-token chat completion) via a
/// trailing button on each row.
class AiKeyModelsProbePage extends StatefulWidget {
  const AiKeyModelsProbePage({
    super.key,
    required this.keyName,
    required this.models,
    required this.onProbeModel,
  });

  final String keyName;

  /// Models list from the key-level probe (GET /models).
  final List<String> models;

  /// Fires a per-model probe; resolves with the result map.
  final Future<Map<String, dynamic>> Function(String model) onProbeModel;

  static Future<void> push(
    BuildContext context, {
    required String keyName,
    required List<String> models,
    required Future<Map<String, dynamic>> Function(String model) onProbeModel,
  }) {
    return Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => AiKeyModelsProbePage(
          keyName: keyName,
          models: models,
          onProbeModel: onProbeModel,
        ),
      ),
    );
  }

  @override
  State<AiKeyModelsProbePage> createState() => _AiKeyModelsProbePageState();
}

class _AiKeyModelsProbePageState extends State<AiKeyModelsProbePage> {
  final Map<String, _ModelProbeState> _states = {};

  _ModelProbeState _stateFor(String model) {
    return _states.putIfAbsent(model, () => _ModelProbeState());
  }

  Future<void> _probeModel(String model) async {
    final state = _stateFor(model);
    if (state.probing) return;
    setState(() => state.probing = true);
    try {
      final result = await widget.onProbeModel(model);
      if (!mounted) return;
      setState(() {
        state.result = result;
        state.probing = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => state.probing = false);
      AppErrors.showSnack(context, e);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final rows = widget.models
        .map((m) => _rowFor(m, l10n))
        .toList(growable: false);

    return AppScaffold(
      title: Text(l10n.aiKeyProbeTitle),
      body: widget.models.isEmpty
          ? EmptyPlaceholder(title: l10n.aiKeyProbeModelsNotReceived)
          : Padding(
              padding: const EdgeInsets.all(AppSpacing.md),
              child: AppEntityCollection(
                rows: rows,
                onOpen: (_) {},
                columns: [
                  AppEntityColumn(
                    id: 'model',
                    label: l10n.aiKeyProbeModelLabel,
                    flex: 3,
                  ),
                  AppEntityColumn(
                    id: 'status',
                    label: l10n.aiKeyProbeStatusCol,
                    flex: 2,
                  ),
                  AppEntityColumn(
                    id: 'action',
                    label: '',
                    width: 56,
                    align: AppEntityColumnAlign.center,
                  ),
                ],
                empty: EmptyPlaceholder(title: l10n.aiKeyProbeModelsNotReceived),
              ),
            ),
    );
  }

  AppEntityRow _rowFor(String model, AppLocalizations l10n) {
    final state = _stateFor(model);
    final result = state.result;
    final status = result?['status'] as String?;
    final latency = result?['latency_ms'];
    final errorCode = result?['error_code'] as String?;
    final errorMsg = result?['error_message'] as String?;

    final statusText = _statusText(l10n, status, latency);
    final statusColor = _statusColor(context, status);

    return AppEntityRow(
      id: model,
      title: model,
      cells: {'status': statusText},
      cellWidgets: {
        'status': statusText.isEmpty
            ? const SizedBox.shrink()
            : Text(
                statusText,
                style: Theme.of(context)
                    .textTheme
                    .bodyMedium
                    ?.copyWith(color: statusColor),
              ),
        'action': state.probing
            ? const SizedBox(
                width: 24,
                height: 24,
                child: CircularProgressIndicator(strokeWidth: 2),
              )
            : IconButton(
                tooltip: l10n.aiKeyProbeRun,
                icon: const Icon(Icons.play_circle_outline_rounded),
                onPressed: state.probing ? null : () => _probeModel(model),
              ),
      },
      subtitle: (status == 'error' || status == 'unavailable') &&
              (errorMsg != null && errorMsg.isNotEmpty)
          ? '$errorCode${errorCode != null && errorCode.isNotEmpty ? ': ' : ''}$errorMsg'
          : null,
    );
  }

  String _statusText(AppLocalizations l10n, String? status, dynamic latency) {
    switch (status) {
      case 'ok':
        final lat = latency == null ? '' : ' · ${latency}ms';
        return '${l10n.aiKeyProbeStatusOk}$lat';
      case 'error':
        return l10n.aiKeyProbeStatusError;
      case 'unavailable':
        return l10n.aiKeyProbeStatusUnavailable;
      default:
        return '';
    }
  }

  Color _statusColor(BuildContext context, String? status) {
    final tokens = context.appColors;
    switch (status) {
      case 'ok':
        return tokens.success;
      case 'error':
        return tokens.danger;
      case 'unavailable':
        return tokens.warning;
      default:
        return Theme.of(context).colorScheme.onSurfaceVariant;
    }
  }
}
