import 'package:flutter/material.dart';

import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/widgets.dart';
import 'package:prodavan/features/auth/presentation/screens/profile_screen.dart';
import 'package:prodavan/features/procurement/presentation/procurement_actions_panel.dart';
import 'package:prodavan/shell/agent_chat_panel.dart';
import 'package:prodavan/shell/app_scope.dart';
import 'package:prodavan/shell/models.dart';

class CabinetShellScreen extends StatelessWidget {
  const CabinetShellScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final state = AppScope.of(context);
    final cabinet = state.activeCabinet;
    final project = state.activeProject;

    return AppScaffold(
      title: Text(project?.displayName ?? cabinet?.displayName ?? 'Prodavan'),
      actions: [
        if (cabinet != null) _CabinetMenu(cabinets: state.cabinets, active: cabinet),
        if (cabinet != null) _ProjectMenu(projects: state.projects, active: project),
        IconButton(
          icon: const Icon(Icons.person_outline),
          onPressed: () {
            Navigator.of(context).push(
              MaterialPageRoute<void>(builder: (_) => const ProfileScreen()),
            );
          },
          tooltip: 'Профиль',
        ),
        IconButton(
          icon: const Icon(Icons.logout),
          onPressed: state.logout,
          tooltip: 'Выйти',
        ),
      ],
      body: project == null
          ? _NoProjectBody(onCreate: () => _showCreateProjectDialog(context))
          : ProjectDashboardBody(project: project, stats: state.projectStats),
      floatingActionButton: cabinet == null
          ? FloatingActionButton.extended(
              onPressed: () => _showCreateCabinetDialog(context),
              icon: const Icon(Icons.add),
              label: const Text('Создать кабинет'),
            )
          : FloatingActionButton(
              onPressed: () => _showCreateCabinetDialog(context),
              tooltip: 'Ещё кабинет',
              child: const Icon(Icons.add_business_outlined),
            ),
    );
  }

  Future<void> _showCreateCabinetDialog(BuildContext context) async {
    final slug = TextEditingController(text: 'workspace');
    final name = TextEditingController(text: 'Рабочее пространство');
    final formKey = GlobalKey<FormState>();
    final state = AppScope.of(context);
    final profiles = await state.listCabinetProfiles();
    if (!context.mounted) return;
    String? profileId =
        profiles.isNotEmpty ? profiles.first['id'] as String? : 'electronics-procurement';
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setLocal) => AlertDialog(
          title: const Text('Новый кабинет'),
          content: AppForm(
            formKey: formKey,
            children: [
              AppTextField(controller: slug, label: 'Slug'),
              AppTextField(controller: name, label: 'Название'),
              DropdownButtonFormField<String>(
                initialValue: profileId,
                decoration: const InputDecoration(labelText: 'Профиль кабинета'),
                items: [
                  for (final p in profiles)
                    DropdownMenuItem(
                      value: p['id'] as String,
                      child: Text('${p['display_name']} (${p['id']})'),
                    ),
                ],
                onChanged: (v) => setLocal(() => profileId = v),
              ),
            ],
          ),
          actions: [
            AppButton(
              label: 'Отмена',
              variant: AppButtonVariant.text,
              expanded: false,
              onPressed: () => Navigator.pop(ctx, false),
            ),
            AppButton(
              label: 'Создать',
              expanded: false,
              onPressed: () => Navigator.pop(ctx, true),
            ),
          ],
        ),
      ),
    );
    if (ok == true && context.mounted && profileId != null) {
      await state.createCabinet(
        slug: slug.text.trim(),
        displayName: name.text.trim(),
        profileId: profileId!,
      );
    }
    slug.dispose();
    name.dispose();
  }

  Future<void> _showCreateProjectDialog(BuildContext context) async {
    final slug = TextEditingController(text: 'client');
    final name = TextEditingController(text: 'Клиент');
    final formKey = GlobalKey<FormState>();
    final state = AppScope.of(context);
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Новый проект'),
        content: AppForm(
          formKey: formKey,
          children: [
            AppTextField(controller: slug, label: 'Slug'),
            AppTextField(controller: name, label: 'Название'),
          ],
        ),
        actions: [
          AppButton(
            label: 'Отмена',
            variant: AppButtonVariant.text,
            expanded: false,
            onPressed: () => Navigator.pop(ctx, false),
          ),
          AppButton(
            label: 'Создать',
            expanded: false,
            onPressed: () => Navigator.pop(ctx, true),
          ),
        ],
      ),
    );
    if (ok == true && context.mounted) {
      await state.createProject(slug: slug.text.trim(), displayName: name.text.trim());
    }
    slug.dispose();
    name.dispose();
  }
}

class _CabinetMenu extends StatelessWidget {
  const _CabinetMenu({required this.cabinets, required this.active});

  final List<CabinetItem> cabinets;
  final CabinetItem active;

  @override
  Widget build(BuildContext context) {
    final state = AppScope.of(context);
    return PopupMenuButton<CabinetItem>(
      tooltip: 'Кабинет',
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 8),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (active.hasS4b) const Icon(Icons.bolt, size: 18),
            Text(active.displayName),
            const Icon(Icons.arrow_drop_down),
          ],
        ),
      ),
      itemBuilder: (ctx) => cabinets
          .map(
            (c) => PopupMenuItem(
              value: c,
              child: Row(
                children: [
                  if (c.hasS4b) const Icon(Icons.bolt, size: 16),
                  const SizedBox(width: 8),
                  Expanded(child: Text(c.displayName)),
                ],
              ),
            ),
          )
          .toList(),
      onSelected: state.switchCabinet,
    );
  }
}

class _ProjectMenu extends StatelessWidget {
  const _ProjectMenu({required this.projects, required this.active});

  final List<ProjectItem> projects;
  final ProjectItem? active;

  @override
  Widget build(BuildContext context) {
    final state = AppScope.of(context);
    if (projects.isEmpty) return const SizedBox.shrink();
    return PopupMenuButton<ProjectItem>(
      tooltip: 'Проект',
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 8),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(active?.displayName ?? 'Проект'),
            const Icon(Icons.arrow_drop_down),
          ],
        ),
      ),
      itemBuilder: (ctx) => projects
          .map(
            (p) => PopupMenuItem(
              value: p,
              child: Text('${p.displayName} (${p.inboxPending} inbox)'),
            ),
          )
          .toList(),
      onSelected: state.openProject,
    );
  }
}

class _NoProjectBody extends StatelessWidget {
  const _NoProjectBody({required this.onCreate});

  final VoidCallback onCreate;

  @override
  Widget build(BuildContext context) {
    final tabs = AppScope.of(context).projectTabs;
    final chatOnly = tabs.length == 1 && tabs.contains('chat');
    return EmptyState(
      title: chatOnly
          ? 'Создайте проект для чата с агентом'
          : 'Создайте проект для загрузки спеки',
      icon: chatOnly ? Icons.chat_bubble_outline : Icons.folder_open_outlined,
      action: AppButton(
        label: 'Новый проект',
        icon: Icons.add,
        expanded: false,
        onPressed: onCreate,
      ),
    );
  }
}

class ProjectDashboardBody extends StatelessWidget {
  const ProjectDashboardBody({
    super.key,
    required this.project,
    required this.stats,
  });

  final ProjectItem project;
  final Map<String, dynamic>? stats;

  @override
  Widget build(BuildContext context) {
    final tabs = AppScope.of(context).projectTabs;
    if (tabs.length <= 1 && tabs.contains('chat')) {
      return Padding(
        padding: const EdgeInsets.all(AppSpacing.lg),
        child: AgentChatPanel(projectId: project.id),
      );
    }
    return DefaultTabController(
      length: tabs.length,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(AppSpacing.lg, AppSpacing.md, AppSpacing.lg, 0),
            child: Text(project.displayName, style: Theme.of(context).textTheme.headlineSmall),
          ),
          TabBar(
            isScrollable: true,
            tabs: [for (final t in tabs) Tab(text: _tabLabel(t))],
          ),
          Expanded(
            child: TabBarView(
              children: [
                for (final t in tabs) _tabBody(context, t),
              ],
            ),
          ),
        ],
      ),
    );
  }

  String _tabLabel(String id) {
    switch (id) {
      case 'chat':
        return 'Чат';
      case 'specs':
        return 'Спеки';
      case 'variants':
        return 'Варианты';
      case 'kp':
        return 'КП';
      case 'equipment':
        return 'Оборудование';
      default:
        return id;
    }
  }

  Widget _tabBody(BuildContext context, String id) {
    switch (id) {
      case 'chat':
        return Padding(
          padding: const EdgeInsets.all(AppSpacing.lg),
          child: AgentChatPanel(projectId: project.id),
        );
      case 'specs':
      case 'variants':
      case 'kp':
      case 'equipment':
        return ProcurementActionsPanel(project: project, stats: stats, focusTab: id);
      default:
        return Center(child: Text('Модуль «$id» не зарегистрирован в shell'));
    }
  }
}
