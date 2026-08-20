import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/widgets.dart';
import 'package:prodavan/features/admin/domain/entities/admin_models.dart';
import 'package:prodavan/features/admin/presentation/screens/admin_user_form_screen.dart';
import 'package:prodavan/features/admin/presentation/screens/admin_users_screen.dart';
import 'package:prodavan/features/auth/presentation/screens/profile_screen.dart';
import 'package:prodavan/shell/app_scope.dart';

class AdminShellScreen extends StatefulWidget {
  const AdminShellScreen({super.key});

  @override
  State<AdminShellScreen> createState() => _AdminShellScreenState();
}

class _AdminShellScreenState extends State<AdminShellScreen> {
  int _index = 0;
  AdminStats? _stats;
  String? _loadError;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _loadStats());
  }

  Future<void> _loadStats() async {
    try {
      final stats = await AppScope.of(context).loadAdminStats();
      if (!mounted) return;
      setState(() {
        _stats = stats;
        _loadError = null;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _loadError = e.toString());
    }
  }

  @override
  Widget build(BuildContext context) {
    final state = AppScope.of(context);
    final pages = [
      _DashboardPage(stats: _stats, error: _loadError, onRefresh: _loadStats),
      const AdminUsersScreen(),
      const ProfileScreen(),
    ];

    return AppScaffold(
      title: Text(['Платформа', 'Пользователи', 'Профиль'][_index]),
      actions: [
        IconButton(
          tooltip: 'Выйти',
          onPressed: state.logout,
          icon: const Icon(Icons.logout),
        ),
      ],
      body: pages[_index],
      floatingActionButton: _index == 1
          ? FloatingActionButton.extended(
              onPressed: () async {
                await Navigator.of(context).push(
                  MaterialPageRoute(builder: (_) => const AdminUserFormScreen()),
                );
                await _loadStats();
              },
              icon: const Icon(Icons.person_add_alt_1),
              label: const Text('Создать'),
            )
          : null,
      bottomNavigationBar: NavigationBar(
        selectedIndex: _index,
        onDestinationSelected: (i) => setState(() => _index = i),
        destinations: const [
          NavigationDestination(icon: Icon(Icons.dashboard_outlined), label: 'Сводка'),
          NavigationDestination(icon: Icon(Icons.people_outline), label: 'Users'),
          NavigationDestination(icon: Icon(Icons.badge_outlined), label: 'Профиль'),
        ],
      ),
    );
  }
}

class _DashboardPage extends StatelessWidget {
  const _DashboardPage({
    required this.stats,
    required this.error,
    required this.onRefresh,
  });

  final AdminStats? stats;
  final String? error;
  final VoidCallback onRefresh;

  @override
  Widget build(BuildContext context) {
    if (error != null) {
      return EmptyState(
        title: 'Не удалось загрузить метрики',
        subtitle: error,
        action: AppButton(label: 'Повторить', onPressed: onRefresh, expanded: false),
      );
    }
    if (stats == null) {
      return const Center(child: CircularProgressIndicator());
    }
    return RefreshIndicator(
      onRefresh: () async => onRefresh(),
      child: GridView.count(
        padding: const EdgeInsets.all(AppSpacing.lg),
        crossAxisCount: MediaQuery.sizeOf(context).width > 900 ? 3 : 2,
        mainAxisSpacing: AppSpacing.md,
        crossAxisSpacing: AppSpacing.md,
        childAspectRatio: 1.6,
        children: [
          StatTile(label: 'Пользователи', value: '${stats!.usersTotal}', icon: Icons.people),
          StatTile(label: 'Активные', value: '${stats!.usersByStatus['active'] ?? 0}', icon: Icons.check_circle_outline),
          StatTile(label: 'Приостановлены', value: '${stats!.usersByStatus['suspended'] ?? 0}', icon: Icons.pause_circle_outline),
          StatTile(label: 'Компании', value: '${stats!.tenantsTotal}', icon: Icons.apartment),
          StatTile(label: 'Кабинеты', value: '${stats!.cabinetsTotal}', icon: Icons.work_outline),
          StatTile(label: 'Проекты', value: '${stats!.projectsTotal}', icon: Icons.folder_open),
        ],
      ),
    );
  }
}
