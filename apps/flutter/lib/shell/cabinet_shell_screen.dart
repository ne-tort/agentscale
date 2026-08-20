import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import 'package:prodavan/shell/app_scope.dart';
import 'package:prodavan/shell/feature_gate.dart';
import 'package:prodavan/shell/models.dart';
import 'package:prodavan/shell/nav_gate.dart';

class CabinetShellScreen extends StatelessWidget {
  const CabinetShellScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final state = AppScope.of(context);
    final cabinet = state.activeCabinet;
    final project = state.activeProject;

    return Scaffold(
      appBar: AppBar(
        title: Text(project?.displayName ?? cabinet?.displayName ?? 'Prodavan'),
        actions: [
          if (cabinet != null) _CabinetMenu(cabinets: state.cabinets, active: cabinet),
          if (cabinet != null) _ProjectMenu(projects: state.projects, active: project),
          IconButton(
            icon: const Icon(Icons.logout),
            onPressed: state.logout,
            tooltip: 'Выйти',
          ),
        ],
      ),
      body: project == null
          ? _NoProjectBody(onCreate: () => _showCreateProjectDialog(context))
          : ProjectDashboardBody(project: project, stats: state.projectStats),
      floatingActionButton: cabinet?.profileId == null
          ? FloatingActionButton.extended(
              onPressed: () => _showCreateCabinetDialog(context),
              icon: const Icon(Icons.add),
              label: const Text('Кабинет закупок'),
            )
          : null,
    );
  }

  Future<void> _showCreateCabinetDialog(BuildContext context) async {
    final slug = TextEditingController(text: 'zakupki');
    final name = TextEditingController(text: 'Закупки');
    final state = AppScope.of(context);
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Новый кабинет'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(controller: slug, decoration: const InputDecoration(labelText: 'Slug')),
            TextField(controller: name, decoration: const InputDecoration(labelText: 'Название')),
          ],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Отмена')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Создать')),
        ],
      ),
    );
    if (ok == true && context.mounted) {
      await state.createCabinet(slug: slug.text.trim(), displayName: name.text.trim());
    }
    slug.dispose();
    name.dispose();
  }

  Future<void> _showCreateProjectDialog(BuildContext context) async {
    final slug = TextEditingController(text: 'client');
    final name = TextEditingController(text: 'Клиент');
    final state = AppScope.of(context);
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Новый проект'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(controller: slug, decoration: const InputDecoration(labelText: 'Slug')),
            TextField(controller: name, decoration: const InputDecoration(labelText: 'Название')),
          ],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Отмена')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Создать')),
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
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          const Icon(Icons.folder_open_outlined, size: 64),
          const SizedBox(height: 16),
          const Text('Создайте проект для загрузки спеки'),
          const SizedBox(height: 16),
          FilledButton.icon(
            onPressed: onCreate,
            icon: const Icon(Icons.add),
            label: const Text('Новый проект'),
          ),
        ],
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
    final inbox = (stats?['inbox_files'] as num?)?.toInt() ?? project.inboxPending;
    final runsByPhase = stats?['runs_by_phase'] as Map<String, dynamic>? ?? {};

    return ListView(
      padding: const EdgeInsets.all(24),
      children: [
        Text(project.displayName, style: Theme.of(context).textTheme.headlineSmall),
        const SizedBox(height: 8),
        Text(project.workspaceKey, style: Theme.of(context).textTheme.bodySmall),
        const SizedBox(height: 24),
        Wrap(
          spacing: 12,
          runSpacing: 12,
          children: [
            _StatChip(icon: Icons.inbox_outlined, label: 'Inbox', value: '$inbox'),
            _StatChip(
              icon: Icons.play_circle_outline,
              label: 'Фазы',
              value: '${runsByPhase.length}',
            ),
            FeatureGate(
              capability: 'specs_kp',
              child: _StatChip(
                icon: Icons.description_outlined,
                label: 'КП',
                value: AppScope.of(context).lastExportPath == null ? 'готово' : 'файл',
              ),
            ),
            NavGate(
              capability: 'procurement.s4b',
              child: _StatChip(icon: Icons.bolt, label: 'S4B', value: 'on'),
            ),
          ],
        ),
        const SizedBox(height: 32),
        if (AppScope.of(context).error != null)
          Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: Text(
              AppScope.of(context).error!,
              style: TextStyle(color: Theme.of(context).colorScheme.error),
            ),
          ),
        if (AppScope.of(context).statusMessage != null)
          Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: Text(AppScope.of(context).statusMessage!),
          ),
        FeatureGate(
          capability: 'specs_kp',
          child: Card(
            child: ListTile(
              leading: const Icon(Icons.storage_outlined),
              title: const Text('Каталог CSV'),
              subtitle: const Text('part_number, title, price, stock · под заказ отбрасывается'),
              trailing: const Icon(Icons.upload),
              onTap: AppScope.of(context).busy ? null : () => _pickCatalog(context),
            ),
          ),
        ),
        Card(
          child: ListTile(
            leading: const Icon(Icons.upload_file_outlined),
            title: const Text('Inbox: спека'),
            subtitle: const Text('csv/txt → ingest → review (цены только из каталога)'),
            trailing: const Icon(Icons.play_arrow),
            onTap: AppScope.of(context).busy ? null : () => _pickSpec(context),
          ),
        ),
        FeatureGate(
          capability: 'specs_kp',
          child: Card(
            child: ListTile(
              leading: const Icon(Icons.description_outlined),
              title: const Text('Экспорт КП'),
              subtitle: Text(AppScope.of(context).lastRunId == null
                  ? 'Сначала прогон до review'
                  : 'Прогон ${AppScope.of(context).lastRunId}'),
              onTap: AppScope.of(context).busy ? null : () => AppScope.of(context).exportKp(),
            ),
          ),
        ),
      ],
    );
  }
}

Future<void> _pickCatalog(BuildContext context) async {
  final picked = await FilePicker.platform.pickFiles(
    type: FileType.custom,
    allowedExtensions: ['csv'],
    withData: true,
  );
  final file = picked?.files.single;
  final bytes = file?.bytes;
  if (bytes == null || !context.mounted) return;
  await AppScope.of(context).uploadCatalog(
    filename: file!.name,
    bytes: bytes,
    slug: _slugFromFilename(file.name),
    displayName: file.name,
  );
}

Future<void> _pickSpec(BuildContext context) async {
  final picked = await FilePicker.platform.pickFiles(
    type: FileType.custom,
    allowedExtensions: ['csv', 'txt', 'xlsx', 'xls'],
    withData: true,
  );
  final file = picked?.files.single;
  final bytes = file?.bytes;
  if (bytes == null || !context.mounted) return;
  await AppScope.of(context).uploadSpecAndRun(filename: file!.name, bytes: bytes);
}

String _slugFromFilename(String name) {
  final base = name.toLowerCase().replaceAll(RegExp(r'\.[^.]+$'), '');
  final slug = base.replaceAll(RegExp(r'[^a-z0-9]+'), '-').replaceAll(RegExp(r'^-+|-+$'), '');
  if (slug.length >= 3) return slug.substring(0, slug.length.clamp(0, 64));
  return 'catalog';
}

class _StatChip extends StatelessWidget {
  const _StatChip({required this.icon, required this.label, required this.value});

  final IconData icon;
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Chip(avatar: Icon(icon, size: 18), label: Text('$label: $value'));
  }
}
