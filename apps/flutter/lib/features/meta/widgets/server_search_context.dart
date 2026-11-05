import 'package:flutter/material.dart';

/// Shows active server-side search filters close to the result set.
///
/// The stock filter is currently controlled from the page AppBar; exposing it
/// here keeps the result context visible while scrolling the catalog list and
/// gives users a direct way to broaden the result set again.
class ServerSearchContext extends StatelessWidget {
  const ServerSearchContext({
    super.key,
    required this.query,
    required this.inStockOnly,
    required this.onClearQuery,
    required this.onShowAllStock,
  });

  final String query;
  final bool inStockOnly;
  final VoidCallback onClearQuery;
  final VoidCallback onShowAllStock;

  @override
  Widget build(BuildContext context) {
    if (query.isEmpty && !inStockOnly) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 4),
      child: Align(
        alignment: Alignment.centerLeft,
        child: Wrap(
          spacing: 8,
          runSpacing: 4,
          children: [
            if (query.isNotEmpty)
              InputChip(
                label: Text(query, overflow: TextOverflow.ellipsis),
                // Explicit: the M3 default delete glyph changed across Flutter
                // releases (clear vs cancel), so pin the affordance here.
                deleteIcon: const Icon(Icons.cancel),
                onDeleted: onClearQuery,
              ),
            if (inStockOnly)
              InputChip(
                avatar: const Icon(Icons.inventory, size: 18),
                label: const Text('Только в наличии'),
                deleteIcon: const Icon(Icons.cancel),
                onDeleted: onShowAllStock,
              ),
          ],
        ),
      ),
    );
  }
}
