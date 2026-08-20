import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/widgets.dart';
import 'package:prodavan/shell/app_scope.dart';

class ProfileScreen extends StatefulWidget {
  const ProfileScreen({super.key});

  @override
  State<ProfileScreen> createState() => _ProfileScreenState();
}

class _ProfileScreenState extends State<ProfileScreen> {
  final _formKey = GlobalKey<FormState>();
  final _passwordFormKey = GlobalKey<FormState>();
  final _loginId = TextEditingController();
  final _companyName = TextEditingController();
  final _contact = TextEditingController();
  final _phone = TextEditingController();
  final _email = TextEditingController();
  final _currentPassword = TextEditingController();
  final _newPassword = TextEditingController();
  bool _hydrated = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_hydrated) return;
    final user = AppScope.of(context).currentUser;
    _loginId.text = user?.loginId ?? '';
    _companyName.text = user?.companyName ?? '';
    _contact.text = user?.contactPerson ?? '';
    _phone.text = user?.phone ?? '';
    _email.text = user?.email ?? '';
    _hydrated = true;
  }

  @override
  void dispose() {
    _loginId.dispose();
    _companyName.dispose();
    _contact.dispose();
    _phone.dispose();
    _email.dispose();
    _currentPassword.dispose();
    _newPassword.dispose();
    super.dispose();
  }

  Future<void> _saveProfile() async {
    if (!_formKey.currentState!.validate()) return;
    final state = AppScope.of(context);
    final emailRaw = _email.text.trim();
    await state.updateProfile(
      contactPerson: _contact.text.trim(),
      phone: _phone.text.trim().isEmpty ? null : _phone.text.trim(),
      email: emailRaw, // empty clears on API
    );
  }

  Future<void> _changePassword() async {
    if (!_passwordFormKey.currentState!.validate()) return;
    final state = AppScope.of(context);
    await state.changePassword(
      currentPassword: _currentPassword.text,
      newPassword: _newPassword.text,
    );
    if (!mounted) return;
    _currentPassword.clear();
    _newPassword.clear();
  }

  @override
  Widget build(BuildContext context) {
    final state = AppScope.of(context);

    return AppScaffold(
      title: const Text('Профиль'),
      body: ListView(
        padding: const EdgeInsets.all(AppSpacing.lg),
        children: [
          AppCard(
            child: AppForm(
              formKey: _formKey,
              children: [
                const AppSectionHeader(
                  title: 'Компания',
                  subtitle: 'ID и название задаёт администратор',
                ),
                AppTextField(
                  label: 'ID компании',
                  controller: _loginId,
                  enabled: false,
                  readOnly: true,
                ),
                AppTextField(
                  label: 'Название компании',
                  controller: _companyName,
                  enabled: false,
                  readOnly: true,
                ),
                const AppSectionHeader(title: 'Контакты'),
                AppTextField(
                  controller: _contact,
                  label: 'Контактное лицо',
                ),
                AppTextField(
                  controller: _phone,
                  label: 'Телефон',
                  keyboardType: TextInputType.phone,
                ),
                AppTextField(
                  controller: _email,
                  label: 'Email (необязательно)',
                  keyboardType: TextInputType.emailAddress,
                ),
                AppAsyncButton(
                  label: 'Сохранить',
                  busy: state.busy,
                  onPressed: state.busy ? null : _saveProfile,
                ),
              ],
            ),
          ),
          const SizedBox(height: AppSpacing.lg),
          AppCard(
            child: AppForm(
              formKey: _passwordFormKey,
              children: [
                const AppSectionHeader(title: 'Смена пароля'),
                AppPasswordField(
                  controller: _currentPassword,
                  label: 'Текущий пароль',
                  validator: (v) =>
                      v != null && v.isNotEmpty ? null : 'Обязательное поле',
                ),
                AppPasswordField(
                  controller: _newPassword,
                  label: 'Новый пароль',
                  validator: (v) =>
                      v != null && v.length >= 8 ? null : 'Минимум 8 символов',
                ),
                AppAsyncButton(
                  label: 'Сменить пароль',
                  busy: state.busy,
                  onPressed: state.busy ? null : _changePassword,
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
