import 'package:flutter/material.dart';

/// UI integration type for AI keys (maps onto provider + api_kind).
class AiKeyIntegrationType {
  const AiKeyIntegrationType._(this.id, this.provider, this.apiKind);

  final String id;
  final String? provider;
  final String? apiKind;

  static const cursorSdk = AiKeyIntegrationType._(
    'cursor_sdk',
    'cursor',
    'cursor_sdk',
  );
  static const codexSdk = AiKeyIntegrationType._(
    'codex_sdk',
    'codex',
    'codex_sdk',
  );
  static const claudeSdk = AiKeyIntegrationType._(
    'claude_agent_sdk',
    'claude_code',
    'claude_agent_sdk',
  );
  static const apiKey = AiKeyIntegrationType._('api_key', null, null);

  static const all = [cursorSdk, codexSdk, claudeSdk, apiKey];

  static const sdkApiKinds = {'cursor_sdk', 'codex_sdk', 'claude_agent_sdk'};

  bool get isApiKey => id == apiKey.id;

  static AiKeyIntegrationType fromKey({
    required String provider,
    required String apiKind,
  }) {
    for (final t in all) {
      if (!t.isApiKey && t.apiKind == apiKind && t.provider == provider) {
        return t;
      }
    }
    if (sdkApiKinds.contains(apiKind)) {
      for (final t in all) {
        if (t.apiKind == apiKind) return t;
      }
    }
    return apiKey;
  }
}

IconData? catalogIconForName(String? name) {
  switch (name) {
    case 'smart_toy_outlined':
      return Icons.smart_toy_outlined;
    case 'psychology_outlined':
      return Icons.psychology_outlined;
    case 'hub_outlined':
      return Icons.hub_outlined;
    case 'terminal_outlined':
      return Icons.terminal_outlined;
    default:
      return Icons.cloud_outlined;
  }
}
