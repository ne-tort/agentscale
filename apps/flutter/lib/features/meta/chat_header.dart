import 'package:flutter/foundation.dart';

/// One chat-header quick action: a module view with `ui_json.chat_header`
/// (e.g. equipment «Бюджетирование»). Rendered as an AppBar action in the
/// project chat workspace and opened through [CabinetModuleHost] with the
/// active chat session.
@immutable
class ChatHeaderButton {
  const ChatHeaderButton({
    required this.moduleId,
    required this.moduleName,
    required this.viewSlug,
    required this.label,
    this.icon,
  });

  final String moduleId;
  final String moduleName;

  /// View slug carrying `ui_json.chat_header` — the module host renders it
  /// directly (e.g. `budget_lines_list`).
  final String viewSlug;

  /// Raw label — plain string or locale map (`{"ru": …, "en": …}`); resolve
  /// at render time with `resolveMetaLabel`.
  final dynamic label;

  /// Icon name for `metaIconFromName` (e.g. `request_quote`).
  final String? icon;
}

/// Collects chat-header buttons from a module `views` meta list.
///
/// A view contributes a button only when its `ui_json.chat_header` is a map
/// with an icon/label; the view slug must be non-empty.
List<ChatHeaderButton> collectChatHeaderButtons(
  Iterable<Map<String, dynamic>> views, {
  String moduleId = '',
  String moduleName = '',
}) {
  final out = <ChatHeaderButton>[];
  for (final view in views) {
    final ui = view['ui_json'];
    if (ui is! Map) continue;
    final header = ui['chat_header'];
    if (header is! Map) continue;
    final viewSlug = view['slug']?.toString() ?? '';
    if (viewSlug.isEmpty) continue;
    out.add(
      ChatHeaderButton(
        moduleId: moduleId,
        moduleName: moduleName,
        viewSlug: viewSlug,
        label: header['label'] ?? view['title'],
        icon: header['icon']?.toString(),
      ),
    );
  }
  return out;
}

/// Fetches the `views` meta document body for a module (project runtime meta).
typedef ChatHeaderViewsFetcher = Future<Object?> Function(String moduleId);

/// Loads chat-header buttons for a project: project modules list + per-module
/// `views` meta document.
///
/// Modules whose meta cannot be fetched are skipped silently — chat-header
/// buttons are optional chrome, failures just mean no button.
Future<List<ChatHeaderButton>> loadProjectChatHeaderButtons({
  required List<Map<String, dynamic>> modules,
  required ChatHeaderViewsFetcher fetchViews,
}) async {
  final out = <ChatHeaderButton>[];
  for (final mod in modules) {
    final moduleId =
        mod['module_id']?.toString() ?? mod['id']?.toString() ?? '';
    if (moduleId.isEmpty) continue;
    final moduleName = mod['name']?.toString() ?? moduleId;
    try {
      final doc = await fetchViews(moduleId);
      final body = doc is Map ? doc['body'] : doc;
      final views = body is Map ? body['items'] : body;
      if (views is! List) continue;
      out.addAll(
        collectChatHeaderButtons(
          views.whereType<Map>().map((v) => Map<String, dynamic>.from(v)),
          moduleId: moduleId,
          moduleName: moduleName,
        ),
      );
    } catch (_) {
      // Silent — no button for unavailable module meta.
    }
  }
  return out;
}
