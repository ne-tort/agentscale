import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_color_tokens.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/widgets.dart';
import 'package:prodavan/shell/app_scope.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _formKey = GlobalKey<FormState>();
  final _loginId = TextEditingController();
  final _password = TextEditingController();

  @override
  void dispose() {
    _loginId.dispose();
    _password.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (!_formKey.currentState!.validate()) return;
    final state = AppScope.of(context);
    await state.login(loginId: _loginId.text.trim(), password: _password.text);
  }

  @override
  Widget build(BuildContext context) {
    final state = AppScope.of(context);
    final theme = Theme.of(context);
    final colors = context.appColors;

    return AppScaffold(
      centerBody: true,
      body: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 420),
        child: AppCard(
          child: AppForm(
            formKey: _formKey,
            children: [
              Text('Prodavan', style: theme.textTheme.headlineSmall),
              Text(
                'Вход по ID компании',
                style: theme.textTheme.bodyMedium?.copyWith(color: colors.muted),
              ),
              const SizedBox(height: AppSpacing.sm),
              AppTextField(
                controller: _loginId,
                label: 'ID компании',
                autofillHints: const [AutofillHints.username],
                textInputAction: TextInputAction.next,
                validator: (v) =>
                    v == null || v.trim().isEmpty ? 'Укажите ID компании' : null,
              ),
              AppPasswordField(
                controller: _password,
                textInputAction: TextInputAction.done,
                validator: (v) =>
                    v != null && v.length >= 8 ? null : 'Минимум 8 символов',
              ),
              AppAsyncButton(
                label: 'Войти',
                busy: state.busy,
                onPressed: state.busy ? null : _submit,
              ),
            ],
          ),
        ),
      ),
    );
  }
}
