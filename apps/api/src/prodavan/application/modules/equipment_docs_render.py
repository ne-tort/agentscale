"""Генерация КП / Спецификации из данных бюджетирования (PDF + XLSX).

Раньше листы КП/Спецификация жили в bundled-шаблоне как формульные ссылки на
«Бюджетирование» (4 строки) и латались XML-хирургией — после 4 позиций шла
«каша», а PDF (Gotenberg/LibreOffice) съезжал: шапки, подвалы, границы,
переносы, размер логотипа. Теперь документы строятся из кода:

- PDF — ReportLab (platypus): детерминированный layout, границы таблиц,
  переносы, полная ширина, русский формат дат, суммы прописью;
- XLSX — openpyxl: листы КП/Спецификация пересоздаются с динамическим числом
  строк, стилями и print-setup (fitToWidth), логотип в адекватном размере.

Реквизиты, которые нельзя вывести из данных (номер договора, стороны, сроки,
адрес поставки…), берутся из таблицы ``document_fields`` (плейсхолдеры вида
{contract_number}); даты и суммы (в т.ч. прописью) подставляются автоматически.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from prodavan.application.documents.editing.xlsx_xml_patch import sanitize_sheet_text

_ASSETS = Path(__file__).resolve().parent / "assets"
FONT_REGULAR = "DejaVuSans"
FONT_BOLD = "DejaVuSans-Bold"

MONTHS_RU = [
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
]

_ONES = [
    "",
    "один",
    "два",
    "три",
    "четыре",
    "пять",
    "шесть",
    "семь",
    "восемь",
    "девять",
    "десять",
    "одиннадцать",
    "двенадцать",
    "тринадцать",
    "четырнадцать",
    "пятнадцать",
    "шестнадцать",
    "семнадцать",
    "восемнадцать",
    "девятнадцать",
]
_ONES_F = [
    "",
    "одна",
    "две",
    "три",
    "четыре",
    "пять",
    "шесть",
    "семь",
    "восемь",
    "девять",
    "десять",
    "одиннадцать",
    "двенадцать",
    "тринадцать",
    "четырнадцать",
    "пятнадцать",
    "шестнадцать",
    "семнадцать",
    "восемнадцать",
    "девятнадцать",
]
_TENS = [
    "",
    "",
    "двадцать",
    "тридцать",
    "сорок",
    "пятьдесят",
    "шестьдесят",
    "семьдесят",
    "восемьдесят",
    "девяносто",
]
_HUNDREDS = [
    "",
    "сто",
    "двести",
    "триста",
    "четыреста",
    "пятьсот",
    "шестьсот",
    "семьсот",
    "восемьсот",
    "девятьсот",
]


def _triad_words(n: int, feminine: bool) -> list[str]:
    out: list[str] = []
    h, rest = divmod(n, 100)
    if h:
        out.append(_HUNDREDS[h])
    if rest >= 20:
        t, o = divmod(rest, 10)
        out.append(_TENS[t])
        rest = o
    if rest:
        out.append((_ONES_F if feminine else _ONES)[rest])
    return [w for w in out if w]


def rubles_in_words(amount: float) -> str:
    """123456.78 → «сто двадцать три тысячи четыреста пятьдесят шесть руб. 78 коп.»"""
    total_kop = int(round(abs(amount) * 100))
    rub, kop = divmod(total_kop, 100)
    parts: list[str] = []
    if rub == 0:
        parts.append("ноль")
    else:
        groups = []
        n = rub
        scale = 0
        while n > 0:
            groups.append((n % 1000, scale))
            n //= 1000
            scale += 1
        for value, sc in reversed(groups):
            if value == 0:
                continue
            if sc == 0:
                parts.extend(_triad_words(value, feminine=False))
            elif sc == 1:
                parts.extend(_triad_words(value, feminine=True))
                parts.append(_plural(value, ["тысяча", "тысячи", "тысяч"]))
            elif sc == 2:
                parts.extend(_triad_words(value, feminine=False))
                parts.append(_plural(value, ["миллион", "миллиона", "миллионов"]))
            else:
                parts.extend(_triad_words(value, feminine=False))
                parts.append(_plural(value, ["миллиард", "миллиарда", "миллиардов"]))
    words = " ".join(parts)
    return f"{words} руб. {kop:02d} коп."


def _plural(n: int, forms: list[str]) -> str:
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return forms[0]
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return forms[1]
    return forms[2]


def fmt_date_ru(d: date | datetime | None = None) -> str:
    d = d or datetime.now(UTC).date()
    if isinstance(d, datetime):
        d = d.date()
    return f"«{d.day:02d}» {MONTHS_RU[d.month - 1]} {d.year} г."


def fmt_money(v: float) -> str:
    return f"{v:,.2f}".replace(",", " ")


# --- поля документа (плейсхолдеры) ------------------------------------------


@dataclass
class DocumentFields:
    """Реквизиты КП/Спецификации, вводимые пользователем (document_fields)."""

    supplier_name: str = 'ООО "ИТ Взлёт"'
    supplier_inn: str = "7716960580"
    supplier_kpp: str = "771601001"
    supplier_address: str = "127282, г. Москва, Чермянский проезд, д. 5, стр. 1"
    supplier_email: str = "info@itvzlet.ru"
    supplier_signatory: str = "Троцкий А.В."
    customer_name: str = 'ООО «Ромашка»'
    customer_signatory: str = "Иванов И.И."
    customer_basis: str = "Устава"
    city: str = "г. Москва"
    spec_number: str = "1"
    app_number: str = "1"
    contract_number: str = "00001"
    delivery_place: str = "склада Заказчика"
    delivery_address: str = "125167, г. Москва, Ленинградский пр., д. 37, пом. 21/10"
    delivery_days: str = "7 (семи)"
    payment_days: str = "30 (тридцати)"
    kp_valid_days: int = 2
    lead_time_note: str = "10-12 недель"
    contract_date: str = ""  # пусто → сегодняшняя дата

    @classmethod
    def from_body(cls, body: dict[str, Any] | None) -> DocumentFields:
        f = cls()
        body = body or {}
        for key, val in body.items():
            if hasattr(f, key) and val not in (None, ""):
                if key == "kp_valid_days":
                    try:
                        setattr(f, key, int(float(val)))
                    except (TypeError, ValueError):
                        continue
                else:
                    setattr(f, key, str(val))
        return f

    def contract_date_str(self) -> str:
        return self.contract_date or fmt_date_ru()


# --- модель данных ----------------------------------------------------------


@dataclass
class DocItem:
    title: str
    qty: float
    price_out: float
    total: float
    part_number: str = ""
    unit: str = "шт."
    lead_time: str = ""


@dataclass
class DocModel:
    items: list[DocItem] = field(default_factory=list)
    fields: DocumentFields = field(default_factory=DocumentFields)
    vat_rate: float = 0.22

    @property
    def total_with_vat(self) -> float:
        return round(sum(i.total for i in self.items), 2)

    @property
    def total_without_vat(self) -> float:
        return round(self.total_with_vat / (1 + self.vat_rate), 2)

    @property
    def vat_amount(self) -> float:
        return round(self.total_with_vat - self.total_without_vat, 2)


def build_doc_model(
    rows: list[dict[str, Any]] | None,
    fields_body: dict[str, Any] | None = None,
) -> DocModel:
    """Модель документа из строк budget_lines (price_in/markup/qty/vat)."""
    model = DocModel(fields=DocumentFields.from_body(fields_body))
    for raw in rows or []:
        body = raw if isinstance(raw, dict) else {}
        price_in = _num(body.get("price_in"))
        if price_in <= 0:
            continue
        qty = _num(body.get("qty"), 1.0) or 1.0
        markup = _num(body.get("markup"), 0.1)
        vat = _num(body.get("vat"), 0.22)
        price_out = round(price_in * (1 + markup), 2)
        model.items.append(
            DocItem(
                title=sanitize_sheet_text(str(body.get("title") or "")).strip()
                or "Не найден",
                qty=qty,
                price_out=price_out,
                total=round(qty * price_out, 2),
                part_number=sanitize_sheet_text(str(body.get("part_number") or "")).strip(),
                lead_time=model.fields.lead_time_note,
            )
        )
        if vat:
            model.vat_rate = vat
    return model


def _num(raw: Any, default: float = 0.0) -> float:
    if raw is None or isinstance(raw, bool):
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


# --- PDF (ReportLab) --------------------------------------------------------

_FONTS_READY = False


def _register_fonts() -> None:
    global _FONTS_READY
    if _FONTS_READY:
        return
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    pdfmetrics.registerFont(TTFont(FONT_REGULAR, str(_ASSETS / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont(FONT_BOLD, str(_ASSETS / "DejaVuSans-Bold.ttf")))
    from reportlab.lib.fonts import addMapping

    addMapping(FONT_REGULAR, 0, 0, FONT_REGULAR)
    addMapping(FONT_REGULAR, 1, 0, FONT_BOLD)
    _FONTS_READY = True


def _styles() -> Any:
    from reportlab.lib.styles import ParagraphStyle

    return {
        "h1": ParagraphStyle(
            "h1", fontName=FONT_BOLD, fontSize=14, leading=18, alignment=1, spaceAfter=6
        ),
        "h2": ParagraphStyle("h2", fontName=FONT_BOLD, fontSize=10.5, leading=14),
        "body": ParagraphStyle("body", fontName=FONT_REGULAR, fontSize=9.5, leading=13),
        "small": ParagraphStyle(
            "small", fontName=FONT_REGULAR, fontSize=8.5, leading=11.5
        ),
        "th": ParagraphStyle(
            "th", fontName=FONT_BOLD, fontSize=8.5, leading=11, alignment=1
        ),
        "td": ParagraphStyle("td", fontName=FONT_REGULAR, fontSize=8.5, leading=11),
        "tdc": ParagraphStyle(
            "tdc", fontName=FONT_REGULAR, fontSize=8.5, leading=11, alignment=1
        ),
        "tdr": ParagraphStyle(
            "tdr", fontName=FONT_REGULAR, fontSize=8.5, leading=11, alignment=2
        ),
        "right": ParagraphStyle(
            "right", fontName=FONT_REGULAR, fontSize=9.5, leading=13, alignment=2
        ),
    }


def _logo(width_mm: float) -> Any:
    from reportlab.platypus import Image

    path = _ASSETS / "logo.png"
    if not path.is_file():
        return None
    img = Image(str(path))
    ratio = img.imageHeight / img.imageWidth
    img.drawWidth = width_mm * 2.8346
    img.drawHeight = width_mm * 2.8346 * ratio
    return img


def _stamp(width_mm: float) -> Any:
    from reportlab.platypus import Image

    path = _ASSETS / "stamp.png"
    if not path.is_file():
        return None
    img = Image(str(path))
    ratio = img.imageHeight / img.imageWidth
    img.drawWidth = width_mm * 2.8346
    img.drawHeight = width_mm * 2.8346 * ratio
    return img


def _table(data: list[list[Any]], widths: list[float], style_extra: list[Any] | None = None) -> Any:
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Table, TableStyle

    tbl = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    style = [
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEEEEE")),
    ]
    if style_extra:
        style.extend(style_extra)
    tbl.setStyle(TableStyle(style))
    _ = mm
    return tbl


def build_kp_pdf(model: DocModel) -> bytes:
    """PDF коммерческого предложения."""
    _register_fonts()
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        BaseDocTemplate,
        Frame,
        PageTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
    )

    st = _styles()
    f = model.fields
    buf = io.BytesIO()
    doc = BaseDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=12 * mm,
        bottomMargin=14 * mm,
        title="Коммерческое предложение",
    )
    frame = Frame(
        doc.leftMargin,
        doc.bottomMargin,
        doc.width,
        doc.height,
        id="main",
    )
    doc.addPageTemplates([PageTemplate(id="kp", frames=[frame])])

    avail = doc.width
    story: list[Any] = []

    # Шапка: логотип слева, реквизиты справа — таблицей без рамки
    logo = _logo(46)
    company = Paragraph(
        f"<b>{f.supplier_name}</b>, ИНН {f.supplier_inn}, КПП {f.supplier_kpp}<br/>"
        f"{f.supplier_address}<br/>{f.supplier_email}",
        st["small"],
    )
    header_rows: list[list[Any]] = [[logo or "", company]]
    header = Table(header_rows, colWidths=[avail * 0.42, avail * 0.58])
    header.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(header)
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("Коммерческое предложение", st["h1"]))
    story.append(Paragraph(f"Дата: {fmt_date_ru()}", st["right"]))
    story.append(Spacer(1, 4 * mm))

    # Таблица позиций — на всю ширину, с переносами и границами
    cols = [Paragraph(t, st["th"]) for t in
            ("№", "Наименование товара", "Кол-во", "Ед. изм.", "Цена за ед., руб.", "Сумма, руб.", "Срок поставки")]
    data = [cols]
    for i, item in enumerate(model.items, start=1):
        data.append([
            Paragraph(str(i), st["tdc"]),
            Paragraph(item.title, st["td"]),
            Paragraph(_qty_str(item.qty), st["tdc"]),
            Paragraph(item.unit, st["tdc"]),
            Paragraph(fmt_money(item.price_out), st["tdr"]),
            Paragraph(fmt_money(item.total), st["tdr"]),
            Paragraph(item.lead_time or f.lead_time_note, st["tdc"]),
        ])
    widths = [
        avail * 0.05, avail * 0.40, avail * 0.09, avail * 0.08,
        avail * 0.13, avail * 0.13, avail * 0.12,
    ]
    story.append(_table(data, widths))
    story.append(Spacer(1, 3 * mm))

    totals = [
        ["Без НДС:", fmt_money(model.total_without_vat)],
        [f"НДС {int(round(model.vat_rate * 100))}%:", fmt_money(model.vat_amount)],
        ["Итого, в руб., с НДС:", fmt_money(model.total_with_vat)],
    ]
    tdata = [[Paragraph(a, st["body"]), Paragraph(b, st["right"])] for a, b in totals]
    tdata[-1][0] = Paragraph(f"<b>{totals[-1][0]}</b>", st["body"])
    tdata[-1][1] = Paragraph(f"<b>{totals[-1][1]}</b>", st["right"])
    ttable = Table(tdata, colWidths=[avail * 0.78, avail * 0.22], hAlign="RIGHT")
    ttable.setStyle(
        TableStyle(
            [
                ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.black),
            ]
        )
    )
    story.append(ttable)
    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph(f"Условия доставки: до {f.delivery_place}", st["body"]))
    story.append(
        Paragraph(f"Срок действия КП: до {fmt_date_ru(_kp_valid_date(f))}", st["body"])
    )
    story.append(Spacer(1, 8 * mm))

    # Подвал: печать + подпись
    stamp = _stamp(40)
    sig = Paragraph(
        f"Генеральный директор<br/><br/><br/>_________________ / {f.supplier_signatory} /",
        st["body"],
    )
    footer = Table([[stamp or "", sig]], colWidths=[avail * 0.4, avail * 0.6])
    footer.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    story.append(footer)

    doc.build(story)
    return buf.getvalue()


def _kp_valid_date(f: DocumentFields) -> date:
    from datetime import timedelta

    return datetime.now(UTC).date() + timedelta(days=max(1, f.kp_valid_days))


def _qty_str(qty: float) -> str:
    return str(int(qty)) if float(qty).is_integer() else f"{qty:g}"


def build_spec_pdf(model: DocModel) -> bytes:
    """PDF спецификации: реквизиты из document_fields, суммы и даты — авто."""
    _register_fonts()
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        BaseDocTemplate,
        Frame,
        PageTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
    )

    st = _styles()
    f = model.fields
    buf = io.BytesIO()
    doc = BaseDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=f"Спецификация № {f.spec_number}",
    )
    doc.addPageTemplates(
        [
            PageTemplate(
                id="spec",
                frames=[
                    Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
                ],
            )
        ]
    )
    avail = doc.width
    story: list[Any] = []

    story.append(Paragraph(f"СПЕЦИФИКАЦИЯ № {f.spec_number}", st["h1"]))
    right = Table(
        [
            [Paragraph(f"Приложение № {f.app_number}", st["small"])],
            [
                Paragraph(
                    f"к Договору поставки № {f.contract_number} от {f.contract_date_str()}",
                    st["small"],
                )
            ],
        ],
        colWidths=[avail * 0.6],
        hAlign="RIGHT",
    )
    right.setStyle(
        TableStyle(
            [
                ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    story.append(right)
    story.append(Spacer(1, 3 * mm))
    place_date = Table(
        [[Paragraph(f.city, st["body"]), Paragraph(fmt_date_ru(), st["right"])]],
        colWidths=[avail * 0.5, avail * 0.5],
    )
    place_date.setStyle(
        TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)])
    )
    story.append(place_date)
    story.append(Spacer(1, 3 * mm))
    story.append(
        Paragraph(
            f"{f.customer_name}, именуемое в дальнейшем «Покупатель», в лице Генерального директора "
            f"{f.customer_signatory}, действующего на основании {f.customer_basis}, с одной стороны, и "
            f"{f.supplier_name}, именуемое в дальнейшем «Поставщик», в лице Генерального директора "
            f"{f.supplier_signatory}, действующего на основании Устава, с другой стороны, совместно "
            "именуемые «Стороны», заключили настоящую Спецификацию о нижеследующем:",
            st["body"],
        )
    )
    story.append(Spacer(1, 3 * mm))
    story.append(
        Paragraph("1. Наименование, количество и стоимость поставляемого Товара:", st["h2"])
    )
    story.append(Spacer(1, 2 * mm))

    vat_pct = int(round(model.vat_rate * 100))
    cols = [
        Paragraph(t, st["th"])
        for t in (
            "№",
            "Наименование Товара",
            "Количество, ед.",
            f"Цена за Товар (с НДС {vat_pct}%), руб.",
            f"Стоимость за Товар (с НДС {vat_pct}%), руб.",
            "Сроки поставки",
        )
    ]
    data = [cols]
    for i, item in enumerate(model.items, start=1):
        data.append(
            [
                Paragraph(str(i), st["tdc"]),
                Paragraph(item.title, st["td"]),
                Paragraph(_qty_str(item.qty), st["tdc"]),
                Paragraph(fmt_money(item.price_out), st["tdr"]),
                Paragraph(fmt_money(item.total), st["tdr"]),
                Paragraph(item.lead_time or f.lead_time_note, st["tdc"]),
            ]
        )
    data.append(
        [
            "",
            Paragraph("Итого, в т.ч. НДС:", st["td"]),
            "",
            "",
            Paragraph(fmt_money(model.total_with_vat), st["tdr"]),
            "",
        ]
    )
    widths = [
        avail * 0.05,
        avail * 0.39,
        avail * 0.12,
        avail * 0.15,
        avail * 0.16,
        avail * 0.13,
    ]
    story.append(
        _table(
            data,
            widths,
            style_extra=[("SPAN", (0, len(data) - 1), (1, len(data) - 1))],
        )
    )
    story.append(Spacer(1, 4 * mm))

    story.append(
        Paragraph(
            f"2. Общая цена Товара по настоящей Спецификации составляет "
            f"{fmt_money(model.total_with_vat)} ({rubles_in_words(model.total_with_vat)}), "
            f"в том числе НДС {vat_pct}% – {fmt_money(model.vat_amount)} "
            f"({rubles_in_words(model.vat_amount)}).",
            st["body"],
        )
    )
    for num, text in (
        (
            3,
            "Стоимость настоящей Спецификации является фиксированной и не может быть изменена в "
            "одностороннем порядке с момента ее подписания сторонами, включает в себя все расходы "
            "Поставщика, в том числе, но не ограничиваясь: стоимость Товара, тары, упаковки, "
            "маркировки, транспортировки, страхования, погрузочно-разгрузочных работ и НДС.",
        ),
        (
            4,
            f"Оплата Товара производится Покупателем на расчетный счет Поставщика не позднее "
            f"{f.payment_days} рабочих дней с момента получения товара.",
        ),
        (
            5,
            f"Срок поставки: не более {f.delivery_days} рабочих дней со дня подписания Спецификации.",
        ),
        (6, f"Адрес поставки: {f.delivery_address}."),
        (7, "Настоящая Спецификация вступает в юридическую силу со дня ее подписания сторонами."),
    ):
        story.append(Spacer(1, 1.5 * mm))
        story.append(Paragraph(f"{num}. {text}", st["body"]))
    from reportlab.platypus import KeepTogether

    sig_flow: list[Any] = [Spacer(1, 6 * mm), Paragraph("Подписи Сторон:", st["h2"]), Spacer(1, 3 * mm)]
    buyer = Paragraph(
        f"Покупатель: {f.customer_name}<br/><br/><br/>"
        f"_______________________ / {f.customer_signatory} /<br/>М.П.",
        st["body"],
    )
    seller_tbl = Table(
        [
            [
                Paragraph(
                    f"Поставщик: {f.supplier_name}<br/><br/><br/>"
                    f"_______________________ / {f.supplier_signatory} /<br/>М.П.",
                    st["body"],
                )
            ],
            [_stamp(38) or ""],
        ],
        colWidths=[avail * 0.45],
    )
    seller_tbl.setStyle(
        TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 2)])
    )
    sig = Table([[buyer, seller_tbl]], colWidths=[avail * 0.5, avail * 0.5])
    sig.setStyle(
        TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)])
    )
    sig_flow.append(sig)
    story.append(KeepTogether(sig_flow))

    doc.build(story)
    return buf.getvalue()


# --- XLSX (openpyxl) --------------------------------------------------------

_THIN = "thin"
_MONEY = "#,##0.00"
_QTY = "#,##0.###"


def _xls_borders() -> Any:
    from openpyxl.styles import Border, Side

    side = Side(style=_THIN, color="000000")
    return Border(left=side, right=side, top=side, bottom=side)


def _setup_page(ws: Any, landscape: bool = False) -> None:
    ws.page_setup.orientation = "landscape" if landscape else "portrait"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_margins.left = 0.4
    ws.page_margins.right = 0.4
    ws.page_margins.top = 0.5
    ws.page_margins.bottom = 0.5


def _fill_kp_sheet(ws: Any, model: DocModel) -> None:
    from openpyxl.drawing.image import Image as XlImage
    from openpyxl.styles import Alignment, Font

    f = model.fields
    widths = {"A": 4, "B": 14, "C": 4, "D": 34, "E": 8, "F": 8, "G": 14, "H": 15, "I": 14}
    for col, w in widths.items():
        ws.column_dimensions[col].width = w

    # Логотип крупно в левом верхнем углу (в шаблоне был ~0.3" — нечитаем в PDF)
    if (_ASSETS / "logo.png").is_file():
        img = XlImage(str(_ASSETS / "logo.png"))
        img.width, img.height = 150, 110  # px (~4x3 см) — крупнее слитой ячейки A1:D4
        ws.add_image(img, "A1")
    ws.merge_cells("A1:D6")
    ws.merge_cells("E1:I4")
    ws["E1"] = (
        f'{f.supplier_name}, ИНН {f.supplier_inn}, КПП {f.supplier_kpp}\n'
        f"{f.supplier_address}\n{f.supplier_email}"
    )
    ws["E1"].alignment = Alignment(wrap_text=True, vertical="top")
    ws["E1"].font = Font(size=9)

    ws["A8"] = "Коммерческое предложение"
    ws["A8"].font = Font(size=14, bold=True)
    ws.merge_cells("A8:I8")
    ws["A8"].alignment = Alignment(horizontal="center")
    ws["H9"] = "Дата"
    ws["I9"] = fmt_date_ru()
    ws["I9"].alignment = Alignment(horizontal="right")

    header = ["№", "Наименование товара", "", "", "Кол-во", "Ед. изм.", "Цена за ед.", "Сумма", "Срок поставки"]
    hr = 11
    for i, h in enumerate(header, start=1):
        c = ws.cell(row=hr, column=i, value=h)
        c.font = Font(bold=True, size=9)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = _xls_borders()
    ws.merge_cells(start_row=hr, start_column=2, end_row=hr, end_column=4)
    ws.row_dimensions[hr].height = 26

    row = hr + 1
    for i, item in enumerate(model.items, start=1):
        ws.cell(row=row, column=1, value=i)
        ws.cell(row=row, column=2, value=item.title)
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=4)
        ws.cell(row=row, column=5, value=item.qty)
        ws.cell(row=row, column=6, value=item.unit)
        ws.cell(row=row, column=7, value=item.price_out)
        ws.cell(row=row, column=8, value=item.total)
        ws.cell(row=row, column=9, value=item.lead_time or f.lead_time_note)
        for col in range(1, 10):
            c = ws.cell(row=row, column=col)
            c.border = _xls_borders()
            c.font = Font(size=9)
            c.alignment = Alignment(
                horizontal="center" if col in (1, 5, 6, 9) else ("right" if col in (7, 8) else "left"),
                vertical="center",
                wrap_text=col == 2,
            )
            if col in (7, 8):
                c.number_format = _MONEY
            if col == 5:
                c.number_format = _QTY
        row += 1

    # Итоги справа под таблицей
    tr = row + 1
    ws.cell(row=tr, column=7, value="Без НДС:")
    ws.cell(row=tr, column=8, value=model.total_without_vat)
    ws.cell(row=tr + 1, column=7, value=f"НДС {int(round(model.vat_rate * 100))}%:")
    ws.cell(row=tr + 1, column=8, value=model.vat_amount)
    ws.cell(row=tr + 2, column=7, value="Итого, в руб., с НДС:")
    ws.cell(row=tr + 2, column=8, value=model.total_with_vat)
    for r in (tr, tr + 1, tr + 2):
        ws.cell(row=r, column=8).number_format = _MONEY
        ws.cell(row=r, column=7).alignment = Alignment(horizontal="right")
        ws.cell(row=r, column=8).alignment = Alignment(horizontal="right")
        ws.cell(row=r, column=7).font = Font(size=9, bold=(r == tr + 2))
        ws.cell(row=r, column=8).font = Font(size=9, bold=(r == tr + 2))

    fr = tr + 4
    ws.cell(row=fr, column=1, value=f"Условия доставки: до {f.delivery_place}")
    ws.cell(row=fr + 1, column=1, value=f"Срок действия КП: до {fmt_date_ru(_kp_valid_date(f))}")
    ws.cell(row=fr + 3, column=1, value="Поставщик:")
    ws.cell(row=fr + 5, column=1, value=f"_________________ / {f.supplier_signatory} /")
    if (_ASSETS / "stamp.png").is_file():
        st = XlImage(str(_ASSETS / "stamp.png"))
        st.width, st.height = 150, 130
        ws.add_image(st, f"D{fr + 3}")
    _setup_page(ws)


def _fill_spec_sheet(ws: Any, model: DocModel) -> None:
    from openpyxl.drawing.image import Image as XlImage
    from openpyxl.styles import Alignment, Font

    f = model.fields
    widths = {"A": 5, "B": 44, "C": 13, "D": 18, "E": 19, "F": 15}
    for col, w in widths.items():
        ws.column_dimensions[col].width = w
    vat_pct = int(round(model.vat_rate * 100))

    ws["A1"] = f"СПЕЦИФИКАЦИЯ № {f.spec_number}"
    ws["A1"].font = Font(size=14, bold=True)
    ws.merge_cells("A1:F1")
    ws["A1"].alignment = Alignment(horizontal="center")
    ws["F3"] = f"Приложение № {f.app_number}"
    ws["F4"] = f"к Договору поставки № {f.contract_number} от {f.contract_date_str()}"
    ws["F3"].alignment = Alignment(horizontal="right")
    ws["F4"].alignment = Alignment(horizontal="right", wrap_text=True)
    ws["A6"] = f.city
    ws["F6"] = fmt_date_ru()
    ws["F6"].alignment = Alignment(horizontal="right")
    ws.merge_cells("A7:F9")
    ws["A7"] = (
        f"{f.customer_name}, именуемое в дальнейшем «Покупатель», в лице Генерального директора "
        f"{f.customer_signatory}, действующего на основании {f.customer_basis}, с одной стороны, и "
        f"{f.supplier_name}, именуемое в дальнейшем «Поставщик», в лице Генерального директора "
        f"{f.supplier_signatory}, действующего на основании Устава, с другой стороны, совместно "
        "именуемые «Стороны», заключили настоящую Спецификацию о нижеследующем:"
    )
    ws["A7"].alignment = Alignment(wrap_text=True, vertical="top")
    ws["A7"].font = Font(size=10)
    ws["A11"] = "1. Наименование, количество и стоимость поставляемого Товара:"
    ws["A11"].font = Font(size=10, bold=True)

    hr = 13
    headers = [
        "№",
        "Наименование Товара",
        "Количество, ед.",
        f"Цена за Товар (с НДС {vat_pct}%), руб.",
        f"Стоимость за Товар (с НДС {vat_pct}%), руб.",
        "Сроки поставки",
    ]
    for i, h in enumerate(headers, start=1):
        c = ws.cell(row=hr, column=i, value=h)
        c.font = Font(bold=True, size=9)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = _xls_borders()
    ws.row_dimensions[hr].height = 30

    row = hr + 1
    for i, item in enumerate(model.items, start=1):
        values = [i, item.title, item.qty, item.price_out, item.total, item.lead_time or f.lead_time_note]
        for col, v in enumerate(values, start=1):
            c = ws.cell(row=row, column=col, value=v)
            c.border = _xls_borders()
            c.font = Font(size=9)
            c.alignment = Alignment(
                horizontal="center" if col in (1, 3, 6) else ("right" if col in (4, 5) else "left"),
                vertical="center",
                wrap_text=col == 2,
            )
            if col in (4, 5):
                c.number_format = _MONEY
            if col == 3:
                c.number_format = _QTY
        row += 1
    ws.cell(row=row, column=2, value="Итого, в т.ч. НДС:")
    ws.cell(row=row, column=5, value=model.total_with_vat)
    ws.cell(row=row, column=5).number_format = _MONEY
    for col in range(1, 7):
        c = ws.cell(row=row, column=col)
        c.border = _xls_borders()
        c.font = Font(size=9, bold=True)
        c.alignment = Alignment(horizontal="right" if col == 5 else "left", vertical="center")
    row += 2

    clauses = [
        f"2. Общая цена Товара по настоящей Спецификации составляет {fmt_money(model.total_with_vat)} "
        f"({rubles_in_words(model.total_with_vat)}), в том числе НДС {vat_pct}% – "
        f"{fmt_money(model.vat_amount)} ({rubles_in_words(model.vat_amount)}).",
        "3. Стоимость настоящей Спецификации является фиксированной и не может быть изменена в "
        "одностороннем порядке с момента ее подписания сторонами, включает в себя все расходы "
        "Поставщика, в том числе, но не ограничиваясь: стоимость Товара, тары, упаковки, маркировки, "
        "транспортировки, страхования, погрузочно-разгрузочных работ и НДС.",
        f"4. Оплата Товара производится Покупателем на расчетный счет Поставщика не позднее "
        f"{f.payment_days} рабочих дней с момента получения товара.",
        f"5. Срок поставки: не более {f.delivery_days} рабочих дней со дня подписания Спецификации.",
        f"6. Адрес поставки: {f.delivery_address}.",
        "7. Настоящая Спецификация вступает в юридическую силу со дня ее подписания сторонами.",
    ]
    for text in clauses:
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
        c = ws.cell(row=row, column=1, value=text)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        c.font = Font(size=10)
        ws.row_dimensions[row].height = 30 if len(text) > 110 else 15
        row += 1

    row += 1
    ws.cell(row=row, column=1, value="Подписи Сторон:").font = Font(size=10, bold=True)
    row += 2
    ws.merge_cells(start_row=row, start_column=1, end_row=row + 4, end_column=3)
    ws.cell(row=row, column=1, value=(
        f"Покупатель: {f.customer_name}\n\n\n_______________________ / {f.customer_signatory} /\nМ.П."
    )).alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells(start_row=row, start_column=4, end_row=row + 4, end_column=6)
    ws.cell(row=row, column=4, value=(
        f"Поставщик: {f.supplier_name}\n\n\n_______________________ / {f.supplier_signatory} /\nМ.П."
    )).alignment = Alignment(wrap_text=True, vertical="top")
    if (_ASSETS / "stamp.png").is_file():
        st = XlImage(str(_ASSETS / "stamp.png"))
        st.width, st.height = 140, 122
        ws.add_image(st, f"E{row + 5}")
    _setup_page(ws)


def build_kp_xlsx(model: DocModel) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "КП"
    _fill_kp_sheet(ws, model)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def build_spec_xlsx(model: DocModel) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Спецификация"
    _fill_spec_sheet(ws, model)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def rebuild_kp_spec_sheets(wb: Any, model: DocModel) -> None:
    """Заменить листы КП/Спецификация в рабочей книге бюджета на сгенерированные."""
    for name, filler in (("КП", _fill_kp_sheet), ("Спецификация", _fill_spec_sheet)):
        if name in wb.sheetnames:
            idx = wb.sheetnames.index(name)
            del wb[name]
            ws = wb.create_sheet(name, idx)
        else:
            ws = wb.create_sheet(name)
        filler(ws, model)
