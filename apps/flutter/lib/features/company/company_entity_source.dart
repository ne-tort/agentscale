import 'package:prodavan/l10n/app_localizations.dart';

/// Whether entity is platform-assigned (canonical or legacy API value).
bool companyEntityPlatformAssigned(String? source) {
  switch (source) {
    case 'platform_assigned':
    case 'platform_bound':
      return true;
    default:
      return false;
  }
}

/// Canonical company-facing entity source labels (see docs/target/00-ownership-matrix.md).
String companyEntitySourceLabel(AppLocalizations l10n, String? source) {
  switch (source) {
    case 'company_local':
    case 'company':
      return l10n.companyKeySourceLocal;
    case 'platform_assigned':
    case 'platform_bound':
      return l10n.companyKeyPlatformBound;
    default:
      return source ?? l10n.commonNotSet;
  }
}

/// Whether a company actor can mutate registry fields for this entity row.
bool companyEntityWritable(Map<String, dynamic> row) => row['writable'] == true;

/// Module is bound to the given cabinet within company org scope.
bool companyModuleBoundToCabinet(Map<String, dynamic> module, String cabinetId) {
  final ids = module['cabinet_ids'];
  if (ids is! List) return false;
  return ids.map((e) => e.toString()).contains(cabinetId);
}
