import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/auth/auth_api_client.dart';

/// Outcome of Auth Service token refresh (never silent null).
sealed class RefreshResult {
  const RefreshResult();

  bool get ok => this is RefreshOk;
}

final class RefreshOk extends RefreshResult {
  const RefreshOk(this.result);
  final AuthApiResult result;
}

final class RefreshFailed extends RefreshResult {
  const RefreshFailed(this.error);
  final Object error;
}

ProdavanApiException? refreshApiError(RefreshResult result) {
  if (result is RefreshFailed && result.error is ProdavanApiException) {
    return result.error as ProdavanApiException;
  }
  return null;
}
