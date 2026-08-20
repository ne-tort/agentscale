import 'package:flutter/material.dart';

abstract final class AppRadii {
  static const double sm = 8;
  static const double md = 12;
  static const double lg = 16;

  static final BorderRadius borderSm = BorderRadius.circular(sm);
  static final BorderRadius borderMd = BorderRadius.circular(md);
  static final BorderRadius borderLg = BorderRadius.circular(lg);
}
