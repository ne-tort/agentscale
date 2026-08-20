import 'package:flutter/material.dart';

import 'package:prodavan/shell/app_scope.dart';

/// Line items + offers for one run (cluster / pipeline debug).
class RunDetailScreen extends StatefulWidget {
  const RunDetailScreen({super.key, required this.projectId, required this.runId});

  final String projectId;
  final String runId;

  @override
  State<RunDetailScreen> createState() => _RunDetailScreenState();
}

class _RunDetailScreenState extends State<RunDetailScreen> with SingleTickerProviderStateMixin {
  late final TabController _tabs;
  bool _loading = true;
  String? _error;
  Map<String, dynamic>? _run;
  List<Map<String, dynamic>> _lineitems = [];
  List<Map<String, dynamic>> _offers = [];

  @override
  void initState() {
    super.initState();
    _tabs = TabController(length: 2, vsync: this);
    _load();
  }

  @override
  void dispose() {
    _tabs.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    final api = AppScope.of(context).specsApi;
    try {
      final run = await api.getRun(projectId: widget.projectId, runId: widget.runId);
      List<Map<String, dynamic>> lines = [];
      List<Map<String, dynamic>> offers = [];
      try {
        final li = await api.listLineitems(projectId: widget.projectId, runId: widget.runId);
        lines = (li['items'] as List<dynamic>? ?? []).cast<Map<String, dynamic>>();
      } catch (_) {}
      try {
        final of = await api.listOffers(projectId: widget.projectId, runId: widget.runId);
        offers = (of['items'] as List<dynamic>? ?? []).cast<Map<String, dynamic>>();
      } catch (_) {}
      setState(() {
        _run = run;
        _lineitems = lines;
        _offers = offers;
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
        title: Text(widget.runId),
        bottom: TabBar(
          controller: _tabs,
          tabs: [
            Tab(text: 'Позиции (${_lineitems.length})'),
            Tab(text: 'Офферы (${_offers.length})'),
          ],
        ),
        actions: [
          IconButton(onPressed: _loading ? null : _load, icon: const Icon(Icons.refresh)),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(child: Text(_error!))
              : Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    if (_run != null)
                      Padding(
                        padding: const EdgeInsets.all(12),
                        child: Text(
                          'Фаза: ${_run!['phase']} · ${_run!['phase_status'] ?? ''}',
                          style: Theme.of(context).textTheme.titleSmall,
                        ),
                      ),
                    Expanded(
                      child: TabBarView(
                        controller: _tabs,
                        children: [
                          _LineitemsList(items: _lineitems),
                          _OffersList(items: _offers),
                        ],
                      ),
                    ),
                  ],
                ),
    );
  }
}

class _LineitemsList extends StatelessWidget {
  const _LineitemsList({required this.items});

  final List<Map<String, dynamic>> items;

  @override
  Widget build(BuildContext context) {
    if (items.isEmpty) {
      return const Center(child: Text('Нет lineitems.json'));
    }
    return ListView.separated(
      itemCount: items.length,
      separatorBuilder: (_, __) => const Divider(height: 1),
      itemBuilder: (ctx, i) {
        final item = items[i];
        final title = item['title']?.toString() ?? item['raw']?.toString() ?? '#$i';
        final pn = item['part_number']?.toString();
        return ListTile(
          dense: true,
          title: Text(title),
          subtitle: pn == null || pn.isEmpty ? null : Text('P/N: $pn'),
        );
      },
    );
  }
}

class _OffersList extends StatelessWidget {
  const _OffersList({required this.items});

  final List<Map<String, dynamic>> items;

  @override
  Widget build(BuildContext context) {
    if (items.isEmpty) {
      return const Center(child: Text('Нет offers.json (или ещё не search)'));
    }
    return ListView.separated(
      itemCount: items.length,
      separatorBuilder: (_, __) => const Divider(height: 1),
      itemBuilder: (ctx, i) {
        final o = items[i];
        final title = o['title']?.toString() ?? o['part_number']?.toString() ?? '#$i';
        final seller = o['seller']?.toString() ?? '';
        final price = o['price'];
        final currency = o['currency']?.toString() ?? '';
        return ListTile(
          dense: true,
          title: Text(title),
          subtitle: Text([seller, if (price != null) '$price $currency'].where((s) => s.isNotEmpty).join(' · ')),
        );
      },
    );
  }
}
