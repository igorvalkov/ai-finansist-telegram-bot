"""Create per-user Excel financial reports with exact Decimal aggregates."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from io import BytesIO
from typing import Any
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, PieChart, Reference
from openpyxl.styles import Alignment, Font, PatternFill


TASHKENT = ZoneInfo("Asia/Tashkent")
CURRENCIES = ("UZS", "USD")
KINDS = ("income", "expense")
MAX_EXPORTED_TEXT_CHARS = 10_000_000
MAX_EXCEL_CELL_TEXT = 32_767

TEXT = {
    "ru": {
        "title_week": "Финансовый отчёт за неделю",
        "title_month": "Финансовый отчёт за месяц",
        "summary": "Итоги за период",
        "currency": "Валюта",
        "income": "Доходы",
        "expense": "Расходы",
        "net": "Итог",
        "category": "Категория расходов",
        "amount": "Сумма",
        "id": "ID",
        "date": "Дата",
        "kind": "Тип",
        "description": "Описание",
        "transactions": "Операции",
        "period": "Период",
        "pie": "Расходы по категориям ({currency})",
        "bar": "Расходы по категориям ({currency})",
        "daily": "Доходы и расходы по дням ({currency})",
        "income_series": "Доходы",
        "expense_series": "Расходы",
        "categories": {
            "groceries": "Продукты", "transport": "Транспорт", "housing": "Жильё",
            "health": "Здоровье", "entertainment": "Развлечения", "salary": "Зарплата",
            "business": "Бизнес", "gift": "Подарок", "other": "Другое",
        },
        "kinds": {"income": "Доход", "expense": "Расход"},
    },
    "uz": {
        "title_week": "Haftalik moliyaviy hisobot",
        "title_month": "Oylik moliyaviy hisobot",
        "summary": "Davr yakunlari",
        "currency": "Valyuta",
        "income": "Daromadlar",
        "expense": "Xarajatlar",
        "net": "Jami",
        "category": "Xarajat toifasi",
        "amount": "Summa",
        "id": "ID",
        "date": "Sana",
        "kind": "Turi",
        "description": "Izoh",
        "transactions": "Operatsiyalar",
        "period": "Davr",
        "pie": "Toifalar bo‘yicha xarajatlar ({currency})",
        "bar": "Toifalar bo‘yicha xarajatlar ({currency})",
        "daily": "Kunlik daromad va xarajat ({currency})",
        "income_series": "Daromadlar",
        "expense_series": "Xarajatlar",
        "categories": {
            "groceries": "Oziq-ovqat", "transport": "Transport", "housing": "Uy-joy",
            "health": "Sog‘liq", "entertainment": "Ko‘ngilochar", "salary": "Maosh",
            "business": "Biznes", "gift": "Sovg‘a", "other": "Boshqa",
        },
        "kinds": {"income": "Daromad", "expense": "Xarajat"},
    },
}


def build_financial_report(
    transactions: list[dict[str, Any]],
    period: str,
    start_date: date,
    end_date_exclusive: date,
    language: str,
) -> tuple[BytesIO, str]:
    """Build a workbook for a [start_date, end_date_exclusive) Tashkent period.

    The input is already user-scoped by the repository layer. Amounts and
    aggregates remain Decimal; displayed exact money values are text. Decimal
    cells are used only on the hidden chart data sheet so Excel can chart them.
    """
    if period not in {"week", "month"}:
        raise ValueError("Unsupported report period")
    if language not in TEXT:
        raise ValueError("Unsupported report language")
    if not isinstance(start_date, date) or isinstance(start_date, datetime):
        raise ValueError("Invalid report start date")
    if not isinstance(end_date_exclusive, date) or isinstance(end_date_exclusive, datetime):
        raise ValueError("Invalid report end date")
    if end_date_exclusive <= start_date:
        raise ValueError("Report end date must be after start date")
    if not isinstance(transactions, list) or len(transactions) > 10_000:
        raise ValueError("Invalid or oversized transaction collection")

    labels = TEXT[language]
    daily_dates = [start_date + timedelta(days=offset)
                   for offset in range((end_date_exclusive - start_date).days)]
    if not daily_dates:
        raise ValueError("Report period is empty")

    normalized: list[tuple[dict[str, Any], date, Decimal]] = []
    income_values: dict[str, list[Decimal]] = {currency: [] for currency in CURRENCIES}
    expense_values: dict[str, list[Decimal]] = {currency: [] for currency in CURRENCIES}
    expense_categories: dict[str, dict[str, list[Decimal]]] = {
        currency: defaultdict(list) for currency in CURRENCIES
    }
    daily_values: dict[date, dict[str, dict[str, list[Decimal]]]] = {
        day: {currency: {kind: [] for kind in KINDS} for currency in CURRENCIES}
        for day in daily_dates
    }

    exported_text_chars = 0
    for transaction in transactions:
        if not isinstance(transaction, dict):
            raise ValueError("Invalid transaction record")
        kind = transaction.get("kind")
        currency = transaction.get("currency")
        if kind not in KINDS or currency not in CURRENCIES:
            raise ValueError("Stored transaction has invalid kind or currency")
        amount = _parse_amount(transaction.get("amount"))
        occurred_at = _parse_datetime(transaction.get("occurred_at"))
        transaction_date = occurred_at.astimezone(TASHKENT).date()
        if not start_date <= transaction_date < end_date_exclusive:
            raise ValueError("Transaction is outside the requested period")
        category = transaction.get("category")
        description = transaction.get("description", "")
        if not isinstance(category, str) or not isinstance(description, str):
            raise ValueError("Invalid transaction text fields")
        if len(category) > MAX_EXCEL_CELL_TEXT or len(description) > MAX_EXCEL_CELL_TEXT:
            raise ValueError("Transaction text exceeds Excel's cell limit")
        exported_text_chars += len(category) + len(description)
        if exported_text_chars > MAX_EXPORTED_TEXT_CHARS:
            raise ValueError("Report text exceeds the allowed size")

        normalized.append((transaction, transaction_date, amount))
        target = income_values if kind == "income" else expense_values
        target[currency].append(amount)
        if kind == "expense":
            expense_categories[currency][category].append(amount)
        daily_values[transaction_date][currency][kind].append(amount)

    totals: dict[str, dict[str, Decimal]] = {}
    for currency in CURRENCIES:
        income = _sum_exact(income_values[currency])
        expense = _sum_exact(expense_values[currency])
        totals[currency] = {"income": income, "expense": expense, "net": income - expense}

    category_totals: dict[str, dict[str, Decimal]] = {}
    for currency in CURRENCIES:
        category_totals[currency] = {
            category: _sum_exact(amounts)
            for category, amounts in expense_categories[currency].items()
        }

    workbook = Workbook()
    summary = workbook.active
    summary.title = "Сводка" if language == "ru" else "Hisobot"
    details = workbook.create_sheet("Операции" if language == "ru" else "Operatsiyalar")
    chart_data = workbook.create_sheet("_chart_data")
    chart_data.sheet_state = "hidden"

    title_key = "title_week" if period == "week" else "title_month"
    visible_end = end_date_exclusive - timedelta(days=1)
    _write_literal(summary.cell(1, 1), labels[title_key])
    summary.cell(1, 1).font = Font(bold=True, size=16, color="FFFFFF")
    summary.cell(1, 1).fill = PatternFill("solid", fgColor="1F4E78")
    _write_literal(summary.cell(2, 1), f"{labels['period']}: {start_date.isoformat()} — {visible_end.isoformat()}")
    summary.cell(2, 1).font = Font(italic=True, color="666666")

    _write_literal(summary.cell(4, 1), labels["summary"])
    summary.cell(4, 1).font = Font(bold=True, size=12)
    summary_headers = [labels["currency"], labels["income"], labels["expense"], labels["net"]]
    _write_headers(summary, 5, summary_headers)
    for row, currency in enumerate(CURRENCIES, start=6):
        _write_literal(summary.cell(row, 1), currency)
        for column, key in enumerate(("income", "expense", "net"), start=2):
            _write_literal(summary.cell(row, column), _decimal_text(totals[currency][key]))

    _write_literal(summary.cell(9, 1), labels["category"])
    summary.cell(9, 1).font = Font(bold=True, size=12)
    _write_headers(summary, 10, [labels["category"], labels["currency"], labels["amount"]])
    row_index = 11
    for currency in CURRENCIES:
        for category, amount in sorted(
            category_totals[currency].items(), key=lambda item: (-item[1], item[0])
        ):
            _write_literal(summary.cell(row_index, 1), _category_label(labels, category))
            _write_literal(summary.cell(row_index, 2), currency)
            _write_literal(summary.cell(row_index, 3), _decimal_text(amount))
            row_index += 1

    summary.freeze_panes = "A5"
    summary.column_dimensions["A"].width = 32
    summary.column_dimensions["B"].width = 16
    summary.column_dimensions["C"].width = 24
    summary.column_dimensions["D"].width = 24
    for column in range(1, 5):
        summary.cell(1, column).fill = PatternFill("solid", fgColor="1F4E78")

    detail_headers = [labels["id"], labels["date"], labels["kind"], labels["currency"], labels["amount"], labels["category"], labels["description"]]
    _write_headers(details, 1, detail_headers)
    details.freeze_panes = "A2"
    details.auto_filter.ref = f"A1:G{max(1, len(normalized) + 1)}"
    for row, (transaction, transaction_date, amount) in enumerate(
        sorted(normalized, key=lambda item: (item[1], int(item[0].get("id", 0)))), start=2
    ):
        details.cell(row, 1, transaction.get("id"))
        _write_literal(details.cell(row, 2), transaction_date.isoformat())
        _write_literal(details.cell(row, 3), labels["kinds"][transaction["kind"]])
        _write_literal(details.cell(row, 4), transaction["currency"])
        _write_literal(details.cell(row, 5), _decimal_text(amount))
        _write_literal(details.cell(row, 6), _category_label(labels, transaction["category"]))
        _write_literal(details.cell(row, 7), transaction.get("description", ""))
    for column, width in {"A": 12, "B": 14, "C": 16, "D": 12, "E": 20, "F": 24, "G": 48}.items():
        details.column_dimensions[column].width = width

    chart_anchors = {"UZS": {"pie": "E4", "bar": "E20", "daily": "E36"},
                     "USD": {"pie": "M4", "bar": "M20", "daily": "M36"}}
    chart_row = 1
    for currency in CURRENCIES:
        category_rows: list[int] = []
        for category, amount in sorted(
            category_totals[currency].items(), key=lambda item: (-item[1], item[0])
        ):
            _write_literal(chart_data.cell(chart_row, 1), _category_label(labels, category))
            chart_data.cell(chart_row, 2, amount)
            category_rows.append(chart_row)
            chart_row += 1
        if category_rows:
            _add_category_charts(
                summary, chart_data, labels, currency, category_rows, chart_anchors[currency]
            )
        _add_daily_chart(
            summary, chart_data, labels, currency, daily_dates, daily_values,
            chart_row, chart_anchors[currency]["daily"],
        )
        chart_row += len(daily_dates) + 3

    for sheet in workbook.worksheets:
        sheet.sheet_view.showGridLines = False
        for row in sheet.iter_rows():
            for cell in row:
                if cell.value is not None:
                    cell.alignment = Alignment(vertical="top", wrap_text=cell.column == 7)

    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    filename = f"finance_report_{period}_{start_date.isoformat()}_{visible_end.isoformat()}.xlsx"
    return output, filename


def _parse_amount(value: object) -> Decimal:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ValueError("Invalid stored transaction amount")
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Invalid stored transaction amount") from exc
    if (
        not amount.is_finite()
        or amount <= 0
        or amount.adjusted() > 18
        or amount.as_tuple().exponent < -18
        or amount.as_tuple().exponent > 18
    ):
        raise ValueError("Invalid stored transaction amount")
    return amount


def _parse_datetime(value: object) -> datetime:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ValueError("Invalid stored transaction date")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Invalid stored transaction date") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TASHKENT)
    return parsed.astimezone(TASHKENT)


def _sum_exact(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    max_adjusted = max(value.adjusted() for value in values)
    min_exponent = min(value.as_tuple().exponent for value in values)
    precision = max(28, max_adjusted - min_exponent + 1 + len(str(len(values))) + 2)
    with localcontext() as context:
        context.prec = precision
        return sum(values, Decimal("0"))


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _category_label(labels: dict[str, Any], category: str) -> str:
    return labels["categories"].get(category, category)


def _literal_value(value: str) -> str:
    stripped = value.lstrip()
    if value.startswith(("\t", "\r")) or (stripped and stripped[0] in "=+-@"):
        return "'" + value
    return value


def _write_literal(cell: Any, value: str) -> None:
    cell.value = _literal_value(value)
    # Force text cells to remain literal even if the value resembles a formula.
    cell.data_type = "s"


def _write_headers(sheet: Any, row: int, values: list[str]) -> None:
    for column, value in enumerate(values, start=1):
        cell = sheet.cell(row, column)
        _write_literal(cell, value)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="4472C4")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _add_category_charts(
    summary: Any,
    chart_data: Any,
    labels: dict[str, Any],
    currency: str,
    category_rows: list[int],
    anchors: dict[str, str],
) -> None:
    first_row, last_row = min(category_rows), max(category_rows)
    categories = Reference(chart_data, min_col=1, min_row=first_row, max_row=last_row)
    values = Reference(chart_data, min_col=2, min_row=first_row, max_row=last_row)

    pie = PieChart()
    pie.add_data(values, titles_from_data=False)
    pie.set_categories(categories)
    pie.title = labels["pie"].format(currency=currency)
    pie.height, pie.width = 8, 14
    summary.add_chart(pie, anchors["pie"])

    bar = BarChart()
    bar.type = "bar"
    bar.style = 10
    bar.add_data(values, titles_from_data=False)
    bar.set_categories(categories)
    bar.title = labels["bar"].format(currency=currency)
    bar.x_axis.title = labels["amount"]
    bar.height, bar.width = 8, 14
    summary.add_chart(bar, anchors["bar"])


def _add_daily_chart(
    summary: Any,
    chart_data: Any,
    labels: dict[str, Any],
    currency: str,
    daily_dates: list[date],
    daily_values: dict[date, dict[str, dict[str, list[Decimal]]]],
    start_row: int,
    anchor: str,
) -> None:
    _write_headers(
        chart_data,
        start_row,
        [labels["date"], labels["income_series"], labels["expense_series"]],
    )
    for offset, day in enumerate(daily_dates, start=1):
        row = start_row + offset
        _write_literal(chart_data.cell(row, 1), day.isoformat())
        chart_data.cell(row, 2, _sum_exact(daily_values[day][currency]["income"]))
        chart_data.cell(row, 3, _sum_exact(daily_values[day][currency]["expense"]))

    chart = LineChart()
    chart.style = 13
    chart.title = labels["daily"].format(currency=currency)
    chart.y_axis.title = labels["amount"]
    chart.x_axis.title = labels["date"]
    data = Reference(chart_data, min_col=2, max_col=3, min_row=start_row, max_row=start_row + len(daily_dates))
    categories = Reference(chart_data, min_col=1, min_row=start_row + 1, max_row=start_row + len(daily_dates))
    chart.add_data(data, titles_from_data=True)
    chart.set_categories(categories)
    chart.height, chart.width = 8, 14
    summary.add_chart(chart, anchor)

