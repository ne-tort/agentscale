import 'package:flutter/material.dart';

import 'package:prodavan/features/runs/presentation/run_detail_screen.dart';
import 'package:prodavan/shell/app_scope.dart';

/// Lists pipeline runs for the active project (cluster debug).
class RunsScreen extends StatefulWidget {
  const RunsScreen({super.key, required this.projectId, required this.projectName});

  final String projectId;
  final String projectName;

  @override
  State<RunsScreen> createState() => _RunsScreenState();
}

class _RunsScreenState extends State<RunsScreen> {
  bool _loading = true;
  String? _error;
  List<Map<String, dynamic>> _items = [];

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final data = await AppScope.of(context).specsApi.listRuns(widget.projectId);
      final raw = data['items'] as List<dynamic>? ?? [];
      setState(() {
        _items = raw.cast<Map<String, dynamic>>();
        _loading = false;
      });
    } catch (exc) {
      setState(() {
        _error = exc.toString();
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text('Прогоны · ${widget.projectName}'),
        actions: [
          IconButton(onPressed: _loading ? null : _load, icon: const Icon(Icons.refresh)),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(child: Text(_error!))
              : _items.isEmpty
                  ? const Center(child: Text('Нет прогонов'))
                  : ListView.separated(
                      itemCount: _items.length,
                      separatorBuilder: (_, __) => const Divider(height: 1),
                      itemBuilder: (ctx, i) {
                        final run = _items[i];
                        final runId = run['run_id']?.toString() ?? '?';
                        final phase = run['phase']?.toString() ?? '';
                        final status = run['phase_status']?.toString() ?? '';
                        return ListTile(
                          title: Text(runId),
                          subtitle: Text('$phase · $status'),
                          trailing: const Icon(Icons.chevron_right),
                          onTap: () {
                            Navigator.of(context).push(
                              MaterialPageRoute<void>(
                                builder: (_) => RunDetailScreen(
                                  projectId: widget.projectId,
                                  runId: runId,
                                ),
                              ),
                            );
                          },
                        );
                      },
                    ),
    );
  }
}
