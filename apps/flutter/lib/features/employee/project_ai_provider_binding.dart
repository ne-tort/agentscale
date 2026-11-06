/// Whether a project's stored AI key is present in the API's eligible-key list.
///
/// The API list is the source of truth for keys that can be used by this
/// project at runtime; a persisted ID absent from it must not enable launch.
bool isProjectAiProviderBindingAvailable(
  String? resolvedKeyId,
  Iterable<Map<String, dynamic>> availableKeys,
) {
  if (resolvedKeyId == null) return false;
  return availableKeys.any((key) => key['id'] == resolvedKeyId);
}
