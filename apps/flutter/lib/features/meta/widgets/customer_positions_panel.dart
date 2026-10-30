import 'package:flutter/material.dart';

import 'package:prodavan/core/api/prodavan_api.dart';
import 'package:prodavan/core/theme/app_spacing.dart';
import 'package:prodavan/core/widgets/app_error_presenter.dart';
import 'package:prodavan/features/meta/module_cell_format.dart';
import 'package:prodavan/l10n/app_localizations.dart';

/// Right-side chat panel «Позиции заказчика»: positions of the current tender
/// chat, found products as sub-items, one selected offer per position.
class CustomerPositionsPanel extends StatefulWidget {
  const CustomerPositionsPanel({
    super.key,
    required this.api,
    required this.cabinetId,
    required this.projectId,
    required this.sessionId,
    this.moduleId = 'mod_equipment',
    this.onRefresh,
  });

  final ProdavanApi api;
  final String cabinetId;
  final String projectId;
  final String sessionId;
  final String moduleId;
  final VoidCallback? onRefresh;

  @override
  State<CustomerPositionsPanel> createState() => _CustomerPositionsPanelState();
}

class _PositionView {
  _PositionView({
    required this.id,
    required this.title,
    required this.partNumber,
    required this.qty,
    required this.selectedOfferId,
  });

  final String id;
  final String title;
  final String partNumber;
  final String qty;
  final String selectedOfferId;
  final List<_OfferView> offers = [];
}

class _OfferView {
  _OfferView({
    required this.id,
    required this.title,
    required this.partNumber,
    required this.seller,
    required this.price,
    required this.currency,
    required this.matchKind,
    required this.isSelected,
    required this.isEffective,
    required this.inStock,
    required this.isStale,
  });

  final String id;
  final String title;
  final String partNumber;
  final String seller;
  final double? price;
  final String currency;
  final String matchKind;
  final bool isSelected;
  final bool isEffective;
  final bool? inStock;
  final bool isStale;
}

class _CustomerPositionsPanelState extends State<CustomerPositionsPanel> {
  bool _loading = true;
  String? _error;
  bool _expanded = true;
  final Set<String> _openOffers = {};
  List<_PositionView> _positions = const [];

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void didUpdateWidget(covariant CustomerPositionsPanel oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.sessionId != widget.sessionId ||
        oldWidget.projectId != widget.projectId) {
      _load();
    }
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final results = await Future.wait([
        widget.api.listProjectRuntimeModuleDataRows(
          projectId: widget.projectId,
          moduleId: widget.moduleId,
          tableSlug: 'request_lines',
          sessionId: widget.sessionId,
        ),
        widget.api.listProjectRuntimeModuleDataRows(
          projectId: widget.projectId,
          moduleId: widget.moduleId,
          tableSlug: 'found_groups',
          sessionId: widget.sessionId,
        ),
        widget.api.listProjectRuntimeModuleDataRows(
          projectId: widget.projectId,
          moduleId: widget.moduleId,
          tableSlug: 'found_offers',
          sessionId: widget.sessionId,
        ),
      ]);
      if (!mounted) return;
      setState(() {
        _positions = _buildPositions(
          requestLines: results[0],
          foundGroups: results[1],
          foundOffers: results[2],
        );
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e.toString();
      });
    }
  }

  List<_PositionView> _buildPositions({
    required List<Map<String, dynamic>> requestLines,
    required List<Map<String, dynamic>> foundGroups,
    required List<Map<String, dynamic>> foundOffers,
  }) {
    final groupLine = <String, String>{};
    for (final group in foundGroups) {
      final body = group['body'] is Map ? group['body'] as Map : const {};
      final gid = group['row_id']?.toString() ?? '';
      final lid = body['line_id']?.toString() ?? '';
      if (gid.isNotEmpty && lid.isNotEmpty) groupLine[gid] = lid;
    }
    final byLine = <String, List<_OfferView>>{};
    for (final row in foundOffers) {
      final body = row['body'] is Map ? row['body'] as Map : const {};
      final lineId =
          body['line_id']?.toString() ??
          groupLine[body['group_id']?.toString() ?? ''] ??
          '';
      if (lineId.isEmpty) continue;
      final offer = _OfferView(
        id: row['row_id']?.toString() ?? '',
        title: _str(body['title']),
        partNumber: _str(body['part_number']),
        seller: _str(body['seller']),
        price: _num(body['price']) ?? _num(body['price_orig']),
        currency: _str(body['currency']).ifEmpty('RUB'),
        matchKind: _str(body['match_kind']),
        isSelected: body['is_selected'] == true,
        isEffective: body['is_effective'] == true,
        inStock: body['in_stock'] is bool ? body['in_stock'] as bool : null,
        isStale: body['is_stale'] == true,
      );
      byLine.putIfAbsent(lineId, () => []).add(offer);
    }
    final positions = <_PositionView>[];
    for (final row in requestLines) {
      final body = row['body'] is Map ? row['body'] as Map : const {};
      final id = row['row_id']?.toString() ?? '';
      final pos = _PositionView(
        id: id,
        title: _str(body['title']).ifEmpty('Позиция'),
        partNumber: _str(body['part_number']),
        qty: _qty(body['qty']),
        selectedOfferId: _str(body['selected_offer_id']),
      );
      final offers = byLine.remove(id) ?? [];
      offers.sort((a, b) {
        if (a.isSelected != b.isSelected) return a.isSelected ? -1 : 1;
        final pa = a.price ?? double.infinity;
        final pb = b.price ?? double.infinity;
        return pa.compareTo(pb);
      });
      pos.offers.addAll(offers);
      positions.add(pos);
    }
    // Orphan offers without a request line (legacy data) — show them too.
    byLine.forEach((lineId, offers) {
      final pos = _PositionView(
        id: lineId,
        title: offers.isNotEmpty ? offers.first.title : 'Позиция',
        partNumber: '',
        qty: '1',
        selectedOfferId: '',
      );
      pos.offers.addAll(offers);
      positions.add(pos);
    });
    return positions;
  }

  Future<void> _selectOffer(String offerId) async {
    try {
      await widget.api.invokeModuleAction(
        cabinetId: widget.cabinetId,
        moduleId: widget.moduleId,
        actionId: 'select_offer_primary',
        rowId: offerId,
        projectId: widget.projectId,
        sessionId: widget.sessionId,
      );
      widget.onRefresh?.call();
      await _load();
    } catch (e) {
      if (mounted) AppErrors.showSnack(context, e);
    }
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final l10n = AppLocalizations.of(context);
    if (_loading) {
      return _shell(
        child: const Center(
          child: Padding(
            padding: EdgeInsets.all(AppSpacing.lg),
            child: CircularProgressIndicator(strokeWidth: 2),
          ),
        ),
      );
    }
    final found = _positions.where((p) => p.selectedOfferId.isNotEmpty).length;
    final header = _header(scheme, l10n, found);
    if (_error != null) {
      return _shell(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            header,
            Padding(
              padding: const EdgeInsets.all(AppSpacing.md),
              child: Text(
                _error!,
                style: TextStyle(color: scheme.error, fontSize: 12),
              ),
            ),
          ],
        ),
      );
    }
    if (!_expanded) {
      return _shell(child: header);
    }
    return _shell(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          header,
          if (_positions.isEmpty)
            Padding(
              padding: const EdgeInsets.all(AppSpacing.md),
              child: Text(
                l10n.chatCustomerPositionsEmpty,
                style: TextStyle(color: scheme.onSurfaceVariant, fontSize: 12),
              ),
            )
          else
            Flexible(
              child: ListView.separated(
                shrinkWrap: true,
                padding: const EdgeInsets.fromLTRB(
                  AppSpacing.sm,
                  0,
                  AppSpacing.sm,
                  AppSpacing.sm,
                ),
                itemCount: _positions.length,
                separatorBuilder: (_, __) => const SizedBox(height: AppSpacing.sm),
                itemBuilder: (context, index) =>
                    _positionCard(context, scheme, l10n, _positions[index]),
              ),
            ),
        ],
      ),
    );
  }

  Widget _shell({required Widget child}) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      width: 296,
      constraints: const BoxConstraints(maxHeight: 440),
      decoration: BoxDecoration(
        color: scheme.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: scheme.outlineVariant.withValues(alpha: .5)),
      ),
      clipBehavior: Clip.antiAlias,
      child: child,
    );
  }

  Widget _header(ColorScheme scheme, AppLocalizations l10n, int found) {
    return InkWell(
      onTap: () => setState(() => _expanded = !_expanded),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(
          AppSpacing.md,
          AppSpacing.sm,
          AppSpacing.sm,
          AppSpacing.sm,
        ),
        child: Row(
          children: [
            Icon(Icons.list_alt_outlined, size: 18, color: scheme.primary),
            const SizedBox(width: AppSpacing.sm),
            Expanded(
              child: Text(
                l10n.chatCustomerPositionsTitle,
                style: const TextStyle(fontWeight: FontWeight.w600),
              ),
            ),
            Text(
              '$found/${_positions.length}',
              style: TextStyle(color: scheme.onSurfaceVariant, fontSize: 12),
            ),
            Icon(
              _expanded ? Icons.unfold_less : Icons.unfold_more,
              size: 18,
              color: scheme.onSurfaceVariant,
            ),
          ],
        ),
      ),
    );
  }

  Widget _positionCard(
    BuildContext context,
    ColorScheme scheme,
    AppLocalizations l10n,
    _PositionView position,
  ) {
    final selected = position.selectedOfferId.isNotEmpty;
    return Container(
      decoration: BoxDecoration(
        color: scheme.surface,
        borderRadius: BorderRadius.circular(10),
        border: Border.all(
          color: selected
              ? scheme.primary.withValues(alpha: .35)
              : scheme.outlineVariant.withValues(alpha: .4),
        ),
      ),
      padding: const EdgeInsets.all(AppSpacing.sm),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(
                selected
                    ? Icons.check_circle_rounded
                    : Icons.radio_button_unchecked,
                size: 16,
                color: selected ? scheme.primary : scheme.onSurfaceVariant,
              ),
              const SizedBox(width: 6),
              Expanded(
                child: Text(
                  position.title,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600),
                ),
              ),
              Text(
                '×${position.qty}',
                style: TextStyle(color: scheme.onSurfaceVariant, fontSize: 11),
              ),
            ],
          ),
          if (position.partNumber.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(left: 22, top: 2),
              child: Text(
                position.partNumber,
                style: TextStyle(color: scheme.onSurfaceVariant, fontSize: 11),
              ),
            ),
          const SizedBox(height: 6),
          if (position.offers.isEmpty)
            Padding(
              padding: const EdgeInsets.only(left: 22),
              child: Text(
                l10n.chatCustomerOffersEmpty,
                style: TextStyle(color: scheme.onSurfaceVariant, fontSize: 11),
              ),
            )
          else
            ...position.offers.take(5).map(
                  (offer) => _offerTile(context, scheme, l10n, position, offer),
                ),
        ],
      ),
    );
  }

  Widget _offerTile(
    BuildContext context,
    ColorScheme scheme,
    AppLocalizations l10n,
    _PositionView position,
    _OfferView offer,
  ) {
    final selected = offer.isSelected || offer.isEffective;
    final isOpen = _openOffers.contains(offer.id);
    final match = _matchStyle(scheme, offer.matchKind, l10n);
    final warning = offer.matchKind == 'analog' || offer.isStale || offer.inStock == false;
    final color = selected
        ? scheme.primary
        : (warning ? scheme.tertiary : scheme.onSurfaceVariant);
    return Padding(
      padding: const EdgeInsets.only(left: 18, top: 4),
      child: InkWell(
        borderRadius: BorderRadius.circular(8),
        onTap: () async {
          setState(() {
            if (isOpen) {
              _openOffers.remove(offer.id);
            } else {
              _openOffers.add(offer.id);
            }
          });
          await _selectOffer(offer.id);
        },
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
          decoration: BoxDecoration(
            color: selected
                ? scheme.primary.withValues(alpha: .08)
                : (warning
                    ? scheme.tertiary.withValues(alpha: .06)
                    : scheme.surfaceContainerHighest.withValues(alpha: .45)),
            borderRadius: BorderRadius.circular(8),
            border: Border.all(
              color: selected
                  ? scheme.primary.withValues(alpha: .45)
                  : scheme.outlineVariant.withValues(alpha: .35),
            ),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Icon(
                    selected ? Icons.check_circle : Icons.inventory_2_outlined,
                    size: 14,
                    color: color,
                  ),
                  const SizedBox(width: 5),
                  Expanded(
                    child: Text(
                      offer.title.isEmpty ? offer.partNumber : offer.title,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        fontSize: 11,
                        fontWeight: selected ? FontWeight.w600 : FontWeight.w400,
                        color: color,
                      ),
                    ),
                  ),
                  if (offer.price != null)
                    Text(
                      formatMoneyRub(offer.price!),
                      style: TextStyle(
                        fontSize: 11,
                        fontWeight: FontWeight.w600,
                        color: selected ? scheme.primary : scheme.onSurface,
                      ),
                    ),
                ],
              ),
              const SizedBox(height: 2),
              Row(
                children: [
                  if (match.label.isNotEmpty)
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
                      decoration: BoxDecoration(
                        color: match.color.withValues(alpha: .12),
                        borderRadius: BorderRadius.circular(4),
                      ),
                      child: Text(
                        match.label,
                        style: TextStyle(fontSize: 9, color: match.color),
                      ),
                    ),
                  if (offer.seller.isNotEmpty) ...[
                    const SizedBox(width: 4),
                    Expanded(
                      child: Text(
                        offer.seller,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(
                          fontSize: 10,
                          color: scheme.onSurfaceVariant,
                        ),
                      ),
                    ),
                  ],
                  Icon(
                    isOpen ? Icons.expand_less : Icons.expand_more,
                    size: 14,
                    color: scheme.onSurfaceVariant,
                  ),
                ],
              ),
              if (isOpen) ...[
                const SizedBox(height: 4),
                Text(
                  '${l10n.chatCustomerOfferPartNumber}: ${offer.partNumber.ifEmpty('—')}',
                  style: TextStyle(fontSize: 10, color: scheme.onSurfaceVariant),
                ),
                Text(
                  '${l10n.chatCustomerOfferSeller}: ${offer.seller.ifEmpty('—')}',
                  style: TextStyle(fontSize: 10, color: scheme.onSurfaceVariant),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }

  ({String label, Color color}) _matchStyle(
    ColorScheme scheme,
    String kind,
    AppLocalizations l10n,
  ) {
    switch (kind) {
      case 'exact':
        return (label: l10n.chatCustomerOfferExact, color: scheme.primary);
      case 'analog':
        return (label: l10n.chatCustomerOfferAnalog, color: scheme.tertiary);
      case 'doubt':
        return (label: l10n.chatCustomerOfferDoubt, color: scheme.error);
      default:
        return (label: '', color: scheme.onSurfaceVariant);
    }
  }

  static String _str(Object? value) => value?.toString().trim() ?? '';

  static double? _num(Object? value) {
    if (value is num) return value.toDouble();
    final raw = value?.toString().replaceAll(',', '.') ?? '';
    return double.tryParse(raw);
  }

  static String _qty(Object? value) {
    final n = _num(value);
    if (n == null) return '1';
    return n == n.truncateToDouble() ? n.toInt().toString() : n.toString();
  }
}

extension _EmptyString on String {
  String ifEmpty(String fallback) => isEmpty ? fallback : this;
}
