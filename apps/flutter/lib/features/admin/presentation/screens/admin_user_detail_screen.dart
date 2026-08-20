import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/widgets.dart';
import 'package:prodavan/features/admin/domain/entities/admin_models.dart';
import 'package:prodavan/shell/app_scope.dart';

class AdminUserDetailScreen extends StatefulWidget {
  const AdminUserDetailScreen({super.key, required this.userId});

  final String userId;

  @override
  State<AdminUserDetailScreen> createState() => _AdminUserDetailScreenState();
}

class _AdminUserDetailScreenState extends State<AdminUserDetailScreen> {
  AdminUser? _user;
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _reload());
  }

  Future<void> _reload() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final user = await AppScope.of(context).loadAdminUser(widget.userId);
      if (!mounted) return;
      setState(() {
        _user = user;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  Future<void> _setStatus(String status) async {
    await AppScope.of(context).updateAdminUser(widget.userId, status: status);
    await _reload();
  }

  Future<void> _delete() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Удалить пользователя?'),
        content: const Text('Мягкое удаление: вход будет закрыт.'),
        actions: [
          AppButton(
            label: 'Отмена',
            variant: AppButtonVariant.text,
            expanded: false,
            onPressed: () => Navigator.pop(ctx, false),
          ),
          AppButton(
            label: 'Удалить',
            expanded: false,
            onPressed: () => Navigator.pop(ctx, true),
          ),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    await AppScope.of(context).deleteAdminUser(widget.userId);
    if (mounted) Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) {
      return const AppScaffold(
        title: Text('Пользователь'),
        body: Center(child: CircularProgressIndicator()),
      );
    }
    if (_error != null || _user == null) {
      return AppScaffold(
        title: const Text('Пользователь'),
        body: EmptyState(
          title: 'Не найден',
          subtitle: _error,
          action: AppButton(label: 'Назад', onPressed: () => Navigator.pop(context), expanded: false),
        ),
      );
    }
    final user = _user!;
    return AppScaffold(
      title: Text(user.companyName),
      body: ListView(
        padding: const EdgeInsets.all(AppSpacing.lg),
        children: [
          AppCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('ID: ${user.loginId}'),
                const SizedBox(height: AppSpacing.sm),
                Text('Статус: ${user.status}'),
                if (user.email != null) ...[
                  const SizedBox(height: AppSpacing.sm),
                  Text('Email: ${user.email}'),
                ],
                if (user.contactPerson != null) ...[
                  const SizedBox(height: AppSpacing.sm),
                  Text('Контакт: ${user.contactPerson}'),
                ],
              ],
            ),
          ),
          const SizedBox(height: AppSpacing.lg),
          if (user.status == 'active')
            AppButton(
              label: 'Приостановить',
              variant: AppButtonVariant.outlined,
              onPressed: () => _setStatus('suspended'),
            ),
          if (user.status == 'suspended') ...[
            const SizedBox(height: AppSpacing.sm),
            AppButton(
              label: 'Активировать',
              onPressed: () => _setStatus('active'),
            ),
          ],
          const SizedBox(height: AppSpacing.sm),
          AppButton(
            label: 'Удалить',
            variant: AppButtonVariant.outlined,
            onPressed: _delete,
          ),
        ],
      ),
    );
  }
}
