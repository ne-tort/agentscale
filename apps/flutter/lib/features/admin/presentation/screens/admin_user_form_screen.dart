import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/widgets.dart';
import 'package:prodavan/shell/app_scope.dart';

class AdminUserFormScreen extends StatefulWidget {
  const AdminUserFormScreen({super.key});

  @override
  State<AdminUserFormScreen> createState() => _AdminUserFormScreenState();
}

class _AdminUserFormScreenState extends State<AdminUserFormScreen> {
  final _formKey = GlobalKey<FormState>();
  final _loginId = TextEditingController();
  final _companyName = TextEditingController();
  final _password = TextEditingController();
  final _contact = TextEditingController();
  final _phone = TextEditingController();
  final _email = TextEditingController();

  @override
  void dispose() {
    _loginId.dispose();
    _companyName.dispose();
    _password.dispose();
    _contact.dispose();
    _phone.dispose();
    _email.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (!_formKey.currentState!.validate()) return;
    final state = AppScope.of(context);
    final ok = await state.createAdminUser(
      loginId: _loginId.text.trim().toLowerCase(),
      companyName: _companyName.text.trim(),
      password: _password.text,
      contactPerson: _contact.text.trim().isEmpty ? null : _contact.text.trim(),
      phone: _phone.text.trim().isEmpty ? null : _phone.text.trim(),
      email: _email.text.trim().isEmpty ? null : _email.text.trim(),
    );
    if (ok && mounted) Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
    final state = AppScope.of(context);
    return AppScaffold(
      title: const Text('Новый пользователь'),
      body: ListView(
        padding: const EdgeInsets.all(AppSpacing.lg),
        children: [
          AppCard(
            child: AppForm(
              formKey: _formKey,
              children: [
                AppTextField(
                  controller: _loginId,
                  label: 'ID компании (login)',
                  validator: (v) {
                    if (v == null || v.trim().length < 2) return 'Минимум 2 символа';
                    if (!RegExp(r'^[a-z0-9][a-z0-9_-]{1,63}$').hasMatch(v.trim().toLowerCase())) {
                      return 'a-z, 0-9, _ и -';
                    }
                    return null;
                  },
                ),
                AppTextField(
                  controller: _companyName,
                  label: 'Название компании',
                  validator: (v) =>
                      v == null || v.trim().isEmpty ? 'Обязательное поле' : null,
                ),
                AppPasswordField(
                  controller: _password,
                  validator: (v) =>
                      v != null && v.length >= 8 ? null : 'Минимум 8 символов',
                ),
                AppTextField(controller: _contact, label: 'Контактное лицо'),
                AppTextField(
                  controller: _phone,
                  label: 'Телефон',
                  keyboardType: TextInputType.phone,
                ),
                AppTextField(
                  controller: _email,
                  label: 'Email',
                  keyboardType: TextInputType.emailAddress,
                ),
                AppAsyncButton(
                  label: 'Создать',
                  busy: state.busy,
                  onPressed: state.busy ? null : _submit,
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
