import 'package:flutter/material.dart';

import 'package:prodavan/shell/app_scope.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _formKey = GlobalKey<FormState>();
  final _email = TextEditingController();
  final _password = TextEditingController();
  final _displayName = TextEditingController(text: 'Оператор');
  final _tenantSlug = TextEditingController();
  final _tenantName = TextEditingController(text: 'Моя компания');
  bool _registerMode = false;

  @override
  void dispose() {
    _email.dispose();
    _password.dispose();
    _displayName.dispose();
    _tenantSlug.dispose();
    _tenantName.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (!_formKey.currentState!.validate()) return;
    final state = AppScope.of(context);
    if (_registerMode) {
      await state.register(
        email: _email.text.trim(),
        password: _password.text,
        displayName: _displayName.text.trim(),
        tenantSlug: _tenantSlug.text.trim(),
        tenantDisplayName: _tenantName.text.trim(),
      );
    } else {
      await state.login(email: _email.text.trim(), password: _password.text);
    }
  }

  @override
  Widget build(BuildContext context) {
    final state = AppScope.of(context);
    return Scaffold(
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 420),
          child: Card(
            margin: const EdgeInsets.all(24),
            child: Padding(
              padding: const EdgeInsets.all(24),
              child: Form(
                key: _formKey,
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Text('Prodavan', style: Theme.of(context).textTheme.headlineSmall),
                    const SizedBox(height: 8),
                    Text(
                      _registerMode ? 'Регистрация tenant' : 'Вход',
                      style: Theme.of(context).textTheme.bodyMedium,
                    ),
                    const SizedBox(height: 24),
                    TextFormField(
                      controller: _email,
                      decoration: const InputDecoration(labelText: 'Email'),
                      keyboardType: TextInputType.emailAddress,
                      validator: (v) =>
                          v == null || v.isEmpty ? 'Укажите email' : null,
                    ),
                    const SizedBox(height: 12),
                    TextFormField(
                      controller: _password,
                      decoration: const InputDecoration(labelText: 'Пароль'),
                      obscureText: true,
                      validator: (v) =>
                          v != null && v.length >= 8 ? null : 'Минимум 8 символов',
                    ),
                    if (_registerMode) ...[
                      const SizedBox(height: 12),
                      TextFormField(
                        controller: _displayName,
                        decoration: const InputDecoration(labelText: 'Имя'),
                      ),
                      const SizedBox(height: 12),
                      TextFormField(
                        controller: _tenantSlug,
                        decoration: const InputDecoration(labelText: 'Slug tenant'),
                        validator: (v) =>
                            v != null && RegExp(r'^[a-z0-9-]+$').hasMatch(v)
                                ? null
                                : 'a-z, 0-9, дефис',
                      ),
                      const SizedBox(height: 12),
                      TextFormField(
                        controller: _tenantName,
                        decoration: const InputDecoration(labelText: 'Название tenant'),
                      ),
                    ],
                    if (state.error != null) ...[
                      const SizedBox(height: 12),
                      Text(state.error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
                    ],
                    const SizedBox(height: 24),
                    FilledButton(
                      onPressed: state.busy ? null : _submit,
                      child: state.busy
                          ? const SizedBox(
                              height: 20,
                              width: 20,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                          : Text(_registerMode ? 'Зарегистрироваться' : 'Войти'),
                    ),
                    TextButton(
                      onPressed: () => setState(() => _registerMode = !_registerMode),
                      child: Text(_registerMode ? 'Уже есть аккаунт' : 'Создать tenant'),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
