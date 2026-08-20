import 'package:prodavan/core/errors/app_failure.dart';
import 'package:prodavan/core/network/api_exception.dart';

abstract final class ErrorMapper {
  static AppFailure fromApiException(ApiException exc) {
    final code = exc.code;
    final raw = exc.message.trim();
    final looksRaw = raw.startsWith('[') || raw.startsWith('{') || raw.contains('type: value_error');

    final byCode = switch (code) {
      'INVALID_CREDENTIALS' => 'Неверный ID компании или пароль',
      'ACCOUNT_SUSPENDED' => 'Аккаунт приостановлен',
      'ACCOUNT_DELETED' => 'Аккаунт удалён',
      'REGISTER_DISABLED' => 'Регистрация закрыта. Обратитесь к администратору',
      'EMAIL_TAKEN' => 'Email уже используется',
      'LOGIN_OR_SLUG_TAKEN' => 'Такой ID компании уже занят',
      'FORBIDDEN' => 'Недостаточно прав',
      'UNAUTHORIZED' => 'Сессия истекла. Войдите снова',
      'VALIDATION_ERROR' => looksRaw || raw.isEmpty ? 'Проверьте введённые данные' : raw,
      _ => null,
    };

    if (byCode != null) {
      return AppFailure(message: byCode, code: code, statusCode: exc.statusCode);
    }

    final byStatus = switch (exc.statusCode) {
      401 => 'Сессия истекла. Войдите снова',
      403 => 'Недостаточно прав',
      404 => 'Не найдено',
      409 => looksRaw || raw.isEmpty ? 'Конфликт данных' : raw,
      410 => 'Операция больше недоступна',
      422 => looksRaw || raw.isEmpty ? 'Проверьте введённые данные' : raw,
      _ => null,
    };

    if (byStatus != null) {
      return AppFailure(message: byStatus, code: code, statusCode: exc.statusCode);
    }

    if (!looksRaw && raw.isNotEmpty) {
      return AppFailure(message: raw, code: code, statusCode: exc.statusCode);
    }

    return AppFailure(
      message: 'Ошибка сервера (${exc.statusCode})',
      code: code,
      statusCode: exc.statusCode,
    );
  }

  static AppFailure fromUnknown(Object error) {
    final text = error.toString();
    if (text.contains('SocketException') || text.contains('ClientException')) {
      return const AppFailure(message: 'Нет связи с сервером', code: 'NETWORK');
    }
    return const AppFailure(message: 'Что-то пошло не так', code: 'UNKNOWN');
  }
}
