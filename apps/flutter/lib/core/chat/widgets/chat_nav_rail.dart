import 'dart:math' as math;

import 'package:flutter/material.dart';

/// One navigation marker: a user prompt and its position in the visual list.
class ChatNavPrompt {
  const ChatNavPrompt({required this.visualIndex, required this.text});

  /// Index in the reverse (visual) list — 0 is the newest row at the bottom.
  final int visualIndex;
  final String text;
}

/// Slim hover-only rail on the right edge of the transcript.
///
/// It is invisible until the pointer is inside the chat, then reveals one tick
/// per user prompt. Clicking a tick jumps to that prompt. This mirrors the
/// DeepSeek Harness / ZCode minimap pattern: no permanent chrome, fast
/// navigation on long agent runs.
class ChatNavRail extends StatefulWidget {
  const ChatNavRail({
    super.key,
    required this.prompts,
    required this.entryCount,
    required this.onJump,
    this.revealed = false,
  });

  final List<ChatNavPrompt> prompts;

  /// Total number of top-level transcript entries (fractions are relative to
  /// the whole chat, not to the number of prompts).
  final int entryCount;

  /// Called with [ChatNavPrompt.visualIndex].
  final void Function(int visualIndex) onJump;

  /// Parent reports pointer presence in the transcript area.
  final bool revealed;

  @override
  State<ChatNavRail> createState() => _ChatNavRailState();
}

class _ChatNavRailState extends State<ChatNavRail> {
  bool _railHover = false;
  int? _hovered;

  bool get _visible => widget.revealed || _railHover;

  double _topFor(int visualIndex, double height) {
    final denom = widget.entryCount > 1 ? widget.entryCount - 1 : 1;
    final fromBottom = (visualIndex / denom).clamp(0.0, 1.0).toDouble();
    return 8 + (1 - fromBottom) * (height - 16);
  }

  @override
  Widget build(BuildContext context) {
    if (widget.prompts.isEmpty) return const SizedBox.shrink();
    final scheme = Theme.of(context).colorScheme;
    return MouseRegion(
      key: const ValueKey('chat-nav-rail'),
      onEnter: (_) => setState(() => _railHover = true),
      onExit: (_) => setState(() {
        _railHover = false;
        _hovered = null;
      }),
      child: AnimatedOpacity(
        opacity: _visible ? 1 : 0,
        duration: const Duration(milliseconds: 160),
        curve: Curves.easeOut,
        child: IgnorePointer(
          ignoring: !_visible,
          child: LayoutBuilder(
            builder: (context, constraints) {
              final height = constraints.maxHeight;
              final hovered = _hovered;
              return Stack(
                clipBehavior: Clip.none,
                children: [
                  // Track.
                  Positioned(
                    top: 8,
                    bottom: 8,
                    right: 9,
                    child: Container(
                      width: 2,
                      decoration: BoxDecoration(
                        color: scheme.onSurfaceVariant.withValues(alpha: 0.20),
                        borderRadius: BorderRadius.circular(1),
                      ),
                    ),
                  ),
                  if (hovered != null &&
                      hovered >= 0 &&
                      hovered < widget.prompts.length)
                    _PreviewCard(
                      prompt: widget.prompts[hovered],
                      top: _topFor(
                        widget.prompts[hovered].visualIndex,
                        height,
                      ).clamp(4.0, height - 86).toDouble(),
                      scheme: scheme,
                    ),
                  for (var i = 0; i < widget.prompts.length; i++)
                    Positioned(
                      right: 0,
                      top: _topFor(widget.prompts[i].visualIndex, height) - 7,
                      width: 28,
                      height: 14,
                      child: Semantics(
                        button: true,
                        label: widget.prompts[i].text,
                        child: MouseRegion(
                          onEnter: (_) => setState(() => _hovered = i),
                          child: GestureDetector(
                            key: ValueKey('chat-nav-tick-$i'),
                            behavior: HitTestBehavior.opaque,
                            onTap: () =>
                                widget.onJump(widget.prompts[i].visualIndex),
                            child: Align(
                              alignment: Alignment.centerRight,
                              child: Container(
                                width: _hovered == i ? 15 : 8,
                                height: _hovered == i ? 3.5 : 2.5,
                                margin: const EdgeInsets.only(right: 8),
                                decoration: BoxDecoration(
                                  color: _hovered == i
                                      ? scheme.primary
                                      : scheme.onSurfaceVariant.withValues(
                                          alpha: 0.55,
                                        ),
                                  borderRadius: BorderRadius.circular(2),
                                ),
                              ),
                            ),
                          ),
                        ),
                      ),
                    ),
                ],
              );
            },
          ),
        ),
      ),
    );
  }
}

class _PreviewCard extends StatelessWidget {
  const _PreviewCard({
    required this.prompt,
    required this.top,
    required this.scheme,
  });

  final ChatNavPrompt prompt;
  final double top;
  final ColorScheme scheme;

  @override
  Widget build(BuildContext context) {
    final text = prompt.text.replaceAll(RegExp(r'\s+'), ' ').trim();
    final excerpt = text.length <= 220 ? text : '${text.substring(0, 220)}…';
    return Positioned(
      right: 30,
      top: top,
      width: math.min(300, MediaQuery.sizeOf(context).width * 0.6),
      child: IgnorePointer(
        child: Material(
          elevation: 6,
          borderRadius: BorderRadius.circular(10),
          color: scheme.surfaceContainerHigh,
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
            child: Text(
              excerpt,
              maxLines: 3,
              overflow: TextOverflow.ellipsis,
              style: Theme.of(context).textTheme.bodySmall
                  ?.copyWith(color: scheme.onSurface, height: 1.35),
            ),
          ),
        ),
      ),
    );
  }
}
