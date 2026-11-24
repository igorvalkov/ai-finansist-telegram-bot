"""Entry point for the AI Finance Assistant Telegram bot."""

import asyncio
import logging
import os
import re
from datetime import date as date_type, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv

from ai_parser import parse_financial_message

from database import (
    add_transaction,
    ensure_user,
    get_balance_totals,
    get_language,
    get_usd_to_uzs_rate,
    get_transactions_for_period,
    delete_transaction,
    get_transaction,
    initialize_database,
    list_transactions,
    set_language,
    set_usd_to_uzs_rate,
    update_transaction,
)
from report_excel import build_financial_report


load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not BOT_TOKEN or BOT_TOKEN == "replace_with_telegram_bot_token":
    raise RuntimeError("Set BOT_TOKEN in your local .env file before starting the bot.")

dp = Dispatcher()


class TransactionFlow(StatesGroup):
    kind = State()
    amount = State()
    currency = State()
    category = State()
    description = State()
    confirmation = State()
    rate = State()


class SavedOperationFlow(StatesGroup):
    viewing = State()
    edit_fields = State()
    edit_kind = State()
    edit_currency = State()
    edit_amount = State()
    edit_description = State()
    edit_date = State()
    edit_category = State()
    edit_confirmation = State()
    delete_confirmation = State()


CATEGORIES = {
    "expense": ["groceries", "transport", "housing", "health", "entertainment", "other"],
    "income": ["salary", "business", "gift", "other"],
}

TEXT = {
    "ru": {
        "choose": "Выберите язык / Tilni tanlang:",
        "welcome": "Здравствуйте! Это AI Финансовый помощник.",
        "menu": "Главное меню. Выберите действие:",
        "operation": "Добавить операцию",
        "transactions": "Мои операции",
        "balance": "Баланс",
        "report": "Отчёт",
        "rate": "Курс USD/UZS",
        "language": "Сменить язык",
        "soon": "Этот раздел добавим на следующем этапе.",
        "help": "Нажмите кнопку в меню или отправьте /start.",
        "choose_kind": "Что записать?",
        "income": "Доход",
        "expense": "Расход",
        "enter_amount": "Введите сумму числом (например, 150000 или 150000.50).",
        "invalid_amount": "Сумма должна быть положительным числом. Попробуйте ещё раз.",
        "choose_currency": "Выберите валюту:",
        "choose_category": "Выберите категорию:",
        "description": "Добавьте короткое описание или нажмите «Пропустить».",
        "skip": "Пропустить",
        "confirm": "Подтвердить",
        "cancel": "Отмена",
        "confirmation": "Проверьте операцию:\n{kind}: {amount} {currency}\nКатегория: {category}\nОписание: {description}\nДата: {date}",
        "saved": "Операция сохранена.",
        "cancelled": "Ввод операции отменён.",
        "ai_unconfigured": "Для разбора сообщений не настроен GROQ_API_KEY в локальном .env.",
        "ai_error": "Не удалось разобрать сообщение сейчас. Попробуйте ещё раз позже.",
        "ai_unsupported": "Не удалось распознать доход или расход. Опишите операцию обычным текстом.",
        "ai_clarify": "Уточните операцию и отправьте её одним сообщением. Не удалось определить: {fields}.",
        "ai_fields": {
            "kind": "тип операции (доход или расход)", "amount": "сумму",
            "currency": "валюту (UZS или USD)", "category": "категорию",
            "date": "дату", "details": "что именно было оплачено или получено",
            "message": "описание операции",
        },
        "ai_too_long": "Сообщение слишком длинное. Отправьте описание операции не длиннее 2000 символов.",
        "stale": "Этот шаг уже завершён. Начните заново через меню.",
        "balance_summary": "Баланс:\nUZS: {uzs}\nUSD: {usd}",
        "balance_total": "Общий эквивалент по заданному курсу: {total} UZS",
        "balance_rate_missing": "Курс USD/UZS не задан. Его можно установить кнопкой «Курс USD/UZS».",
        "rate_prompt": "Введите, сколько сум приходится на 1 USD (например, 12900 или 12900,50).",
        "rate_current": "Текущий курс: 1 USD = {rate} UZS.\nВведите новый курс или отмените действие.",
        "rate_invalid": "Введите положительный курс числом, например 12900 или 12900,50.",
        "rate_saved": "Курс сохранён: 1 USD = {rate} UZS.",
        "rate_cancelled": "Изменение курса отменено.",
        "report_choose": "Выберите период отчёта:",
        "report_week": "Эта неделя",
        "report_month": "Этот месяц",
        "report_empty": "За выбранный период операций нет.",
        "report_error": "Не удалось сформировать отчёт. За период слишком много операций или возникла ошибка.",
        "report_private_only": "Финансовый отчёт можно скачать только в личном чате с ботом.",
        "transactions_empty": "Пока нет сохранённых операций.",
        "transactions_title": "Ваши операции (страница {page}):",
        "transaction_details": "Операция №{id}\n{kind}: {amount} {currency}\nКатегория: {category}\nОписание: {description}\nДата: {date}",
        "edit_transaction": "Изменить",
        "delete_transaction": "Удалить",
        "back": "К списку",
        "edit_choose_field": "Что изменить?",
        "edit_kind": "Тип операции",
        "edit_amount": "Сумма",
        "edit_currency": "Валюта",
        "edit_category": "Категория",
        "edit_description": "Описание",
        "edit_date": "Дата",
        "enter_new_amount": "Введите новую положительную сумму.",
        "enter_new_description": "Введите новое описание (до 1000 символов). Для пустого описания отправьте —",
        "enter_new_date": "Введите новую дату в формате ГГГГ-ММ-ДД.",
        "invalid_new_amount": "Некорректная сумма. Введите положительное число (до 18 цифр и 6 знаков после запятой).",
        "invalid_new_description": "Описание должно быть не длиннее 1000 символов.",
        "invalid_new_date": "Дата должна быть в формате ГГГГ-ММ-ДД.",
        "edit_review": "Подтвердите изменение операции №{id}:\n{kind}: {amount} {currency}\nКатегория: {category}\nОписание: {description}\nДата: {date}",
        "edit_confirm": "Подтвердить изменение",
        "edit_saved": "Операция обновлена.",
        "edit_cancelled": "Изменение отменено.",
        "delete_review": "Вы уверены, что хотите удалить эту операцию?\n{details}",
        "delete_confirm": "Да, удалить",
        "delete_saved": "Операция удалена.",
        "transaction_not_found": "Операция не найдена. Возможно, список устарел.",
        "stale_edit": "Операция изменилась после открытия. Изменения не применены; откройте её снова.",
        "categories": {
            "groceries": "Продукты", "transport": "Транспорт", "housing": "Жильё",
            "health": "Здоровье", "entertainment": "Развлечения", "salary": "Зарплата",
            "business": "Бизнес", "gift": "Подарок", "other": "Другое",
        },
    },
    "uz": {
        "choose": "Tilni tanlang / Выберите язык:",
        "welcome": "Assalomu alaykum! Bu AI Moliyaviy yordamchi.",
        "menu": "Asosiy menyu. Amalni tanlang:",
        "operation": "Operatsiya qo‘shish",
        "transactions": "Operatsiyalarim",
        "balance": "Balans",
        "report": "Hisobot",
        "rate": "USD/UZS kursi",
        "language": "Tilni almashtirish",
        "soon": "Bu bo‘lim keyingi bosqichda qo‘shiladi.",
        "help": "Menyudagi tugmani bosing yoki /start yuboring.",
        "choose_kind": "Nimani yozamiz?",
        "income": "Daromad",
        "expense": "Xarajat",
        "enter_amount": "Summani kiriting (masalan, 150000 yoki 150000.50).",
        "invalid_amount": "Summa musbat son bo‘lishi kerak. Qayta kiriting.",
        "choose_currency": "Valyutani tanlang:",
        "choose_category": "Kategoriyani tanlang:",
        "description": "Qisqa izoh yozing yoki «O‘tkazib yuborish» tugmasini bosing.",
        "skip": "O‘tkazib yuborish",
        "confirm": "Tasdiqlash",
        "cancel": "Bekor qilish",
        "confirmation": "Operatsiyani tekshiring:\n{kind}: {amount} {currency}\nKategoriya: {category}\nIzoh: {description}\nSana: {date}",
        "saved": "Operatsiya saqlandi.",
        "cancelled": "Operatsiya kiritish bekor qilindi.",
        "ai_unconfigured": "Xabarlarni tahlil qilish uchun lokal .env faylida GROQ_API_KEY sozlanmagan.",
        "ai_error": "Xabarni hozir tahlil qilib bo‘lmadi. Keyinroq qayta urinib ko‘ring.",
        "ai_unsupported": "Daromad yoki xarajatni aniqlab bo‘lmadi. Operatsiyani oddiy matnda tasvirlang.",
        "ai_clarify": "Operatsiyani aniqlashtirib, bitta xabarda qayta yuboring. Aniqlanmagan: {fields}.",
        "ai_fields": {
            "kind": "operatsiya turi (daromad yoki xarajat)", "amount": "summa",
            "currency": "valyuta (UZS yoki USD)", "category": "toifa",
            "date": "sana", "details": "nima uchun to‘lov qilingan yoki daromad olinganini",
            "message": "operatsiya tavsifi",
        },
        "ai_too_long": "Xabar juda uzun. Operatsiyani 2000 belgidan oshmagan matnda yuboring.",
        "stale": "Bu qadam allaqachon yakunlangan. Menyu orqali qaytadan boshlang.",
        "balance_summary": "Balans:\nUZS: {uzs}\nUSD: {usd}",
        "balance_total": "Belgilangan kurs bo‘yicha jami: {total} UZS",
        "balance_rate_missing": "USD/UZS kursi belgilanmagan. Uni «USD/UZS kursi» tugmasi orqali o‘rnating.",
        "rate_prompt": "1 USD necha so‘m ekanini kiriting (masalan, 12900 yoki 12900,50).",
        "rate_current": "Joriy kurs: 1 USD = {rate} UZS.\nYangi kursni kiriting yoki amalni bekor qiling.",
        "rate_invalid": "Musbat kursni kiriting, masalan 12900 yoki 12900,50.",
        "rate_saved": "Kurs saqlandi: 1 USD = {rate} UZS.",
        "rate_cancelled": "Kursni o‘zgartirish bekor qilindi.",
        "report_choose": "Hisobot davrini tanlang:",
        "report_week": "Bu hafta",
        "report_month": "Bu oy",
        "report_empty": "Tanlangan davrda operatsiyalar yo‘q.",
        "report_error": "Hisobotni yaratib bo‘lmadi. Davrda juda ko‘p operatsiya yoki xatolik yuz berdi.",
        "report_private_only": "Moliyaviy hisobotni faqat bot bilan shaxsiy chatda yuklab olish mumkin.",
        "transactions_empty": "Hozircha saqlangan operatsiyalar yo‘q.",
        "transactions_title": "Operatsiyalaringiz ({page}-sahifa):",
        "transaction_details": "Operatsiya №{id}\n{kind}: {amount} {currency}\nToifa: {category}\nIzoh: {description}\nSana: {date}",
        "edit_transaction": "Tahrirlash",
        "delete_transaction": "O‘chirish",
        "back": "Ro‘yxatga",
        "edit_choose_field": "Nimani o‘zgartiramiz?",
        "edit_kind": "Operatsiya turi",
        "edit_amount": "Summa",
        "edit_currency": "Valyuta",
        "edit_category": "Toifa",
        "edit_description": "Izoh",
        "edit_date": "Sana",
        "enter_new_amount": "Yangi musbat summani kiriting.",
        "enter_new_description": "Yangi izohni kiriting (1000 belgigacha). Bo‘sh izoh uchun — yuboring",
        "enter_new_date": "Yangi sanani YYYY-MM-DD formatida kiriting.",
        "invalid_new_amount": "Summa noto‘g‘ri. Musbat son kiriting (18 raqam va kasrdan keyin 6 belgigacha).",
        "invalid_new_description": "Izoh 1000 belgidan oshmasligi kerak.",
        "invalid_new_date": "Sana YYYY-MM-DD formatida bo‘lishi kerak.",
        "edit_review": "№{id} operatsiya o‘zgarishini tasdiqlang:\n{kind}: {amount} {currency}\nToifa: {category}\nIzoh: {description}\nSana: {date}",
        "edit_confirm": "O‘zgarishni tasdiqlash",
        "edit_saved": "Operatsiya yangilandi.",
        "edit_cancelled": "Tahrirlash bekor qilindi.",
        "delete_review": "Ushbu operatsiyani o‘chirishni xohlaysizmi?\n{details}",
        "delete_confirm": "Ha, o‘chirish",
        "delete_saved": "Operatsiya o‘chirildi.",
        "transaction_not_found": "Operatsiya topilmadi. Ro‘yxat eskirgan bo‘lishi mumkin.",
        "stale_edit": "Operatsiya ochilgandan keyin o‘zgargan. O‘zgarishlar qo‘llanmadi; uni qayta oching.",
        "categories": {
            "groceries": "Oziq-ovqat", "transport": "Transport", "housing": "Uy-joy",
            "health": "Sog‘liq", "entertainment": "Ko‘ngilochar", "salary": "Maosh",
            "business": "Biznes", "gift": "Sovg‘a", "other": "Boshqa",
        },
    },
}


def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Русский", callback_data="lang:ru"),
        InlineKeyboardButton(text="O‘zbekcha", callback_data="lang:uz"),
    ]])


def main_menu(language: str) -> InlineKeyboardMarkup:
    labels = TEXT[language]
    buttons = [
        ("operation", "menu:operation"),
        ("balance", "menu:balance"),
        ("transactions", "menu:transactions"),
        ("report", "menu:report"),
        ("rate", "menu:rate"),
        ("language", "menu:language"),
    ]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=labels[key], callback_data=data)]
        for key, data in buttons
    ])


def kind_keyboard(language: str) -> InlineKeyboardMarkup:
    labels = TEXT[language]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=labels["income"], callback_data="txkind:income")],
        [InlineKeyboardButton(text=labels["expense"], callback_data="txkind:expense")],
        [InlineKeyboardButton(text=labels["cancel"], callback_data="tx:cancel")],
    ])


def currency_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="UZS — so‘m", callback_data="txcurrency:UZS"),
        InlineKeyboardButton(text="USD — $", callback_data="txcurrency:USD"),
    ], [InlineKeyboardButton(text=TEXT[language]["cancel"], callback_data="tx:cancel")]])


def category_keyboard(language: str, kind: str) -> InlineKeyboardMarkup:
    labels = TEXT[language]["categories"]
    rows = [[InlineKeyboardButton(text=labels[key], callback_data=f"txcategory:{key}")]
            for key in CATEGORIES[kind]]
    rows.append([InlineKeyboardButton(text=TEXT[language]["cancel"], callback_data="tx:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def transaction_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=TEXT[language]["confirm"], callback_data="tx:confirm"),
        InlineKeyboardButton(text=TEXT[language]["cancel"], callback_data="tx:cancel"),
    ]])


def rate_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=TEXT[language]["cancel"], callback_data="rate:cancel")
    ]])


def transaction_list_keyboard(rows: list[dict], offset: int, language: str) -> InlineKeyboardMarkup:
    buttons = []
    for item in rows:
        kind = TEXT[language][item["kind"]]
        label = f"{item['occurred_at'][:10]} · {kind} · {format_decimal(Decimal(item['amount']), language)} {item['currency']}"
        buttons.append([InlineKeyboardButton(text=label[:64], callback_data=f"saved:open:{item['id']}")])
    navigation = []
    if offset > 0:
        navigation.append(InlineKeyboardButton(text="‹", callback_data=f"saved:list:{max(0, offset - 10)}"))
    if len(rows) == 10:
        navigation.append(InlineKeyboardButton(text="›", callback_data=f"saved:list:{offset + 10}"))
    if navigation:
        buttons.append(navigation)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def transaction_detail_keyboard(transaction_id: int, language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=TEXT[language]["edit_transaction"], callback_data=f"saved:edit:{transaction_id}")],
        [InlineKeyboardButton(text=TEXT[language]["delete_transaction"], callback_data=f"saved:delete:{transaction_id}")],
        [InlineKeyboardButton(text=TEXT[language]["back"], callback_data="saved:list:0")],
    ])


def edit_fields_keyboard(transaction_id: int, language: str) -> InlineKeyboardMarkup:
    labels = TEXT[language]
    fields = ["kind", "amount", "currency", "category", "description", "date"]
    rows = [[InlineKeyboardButton(text=labels[f"edit_{field}"], callback_data=f"saved:field:{field}:{transaction_id}")]
            for field in fields]
    rows.append([InlineKeyboardButton(text=labels["back"], callback_data=f"saved:open:{transaction_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def edit_confirmation_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=TEXT[language]["edit_confirm"], callback_data="saved:edit-confirm"),
        InlineKeyboardButton(text=TEXT[language]["cancel"], callback_data="saved:edit-cancel"),
    ]])


def delete_confirmation_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=TEXT[language]["delete_confirm"], callback_data="saved:delete-confirm"),
        InlineKeyboardButton(text=TEXT[language]["cancel"], callback_data="saved:delete-cancel"),
    ]])


def saved_transaction_text(transaction: dict, language: str, template_key: str = "transaction_details") -> str:
    occurred_at = datetime.fromisoformat(transaction["occurred_at"])
    return TEXT[language][template_key].format(
        id=transaction["id"],
        kind=TEXT[language][transaction["kind"]],
        amount=format_decimal(Decimal(transaction["amount"]), language),
        currency=transaction["currency"],
        category=TEXT[language]["categories"].get(transaction["category"], transaction["category"]),
        description=transaction["description"] or "—",
        date=occurred_at.strftime("%Y-%m-%d"),
    )


async def show_saved_transactions(message: Message, telegram_user_id: int, language: str, offset: int = 0) -> None:
    rows = list_transactions(telegram_user_id, limit=10, offset=offset)
    if not rows:
        await message.answer(TEXT[language]["transactions_empty"])
        return
    await message.answer(
        TEXT[language]["transactions_title"].format(page=offset // 10 + 1),
        reply_markup=transaction_list_keyboard(rows, offset, language),
    )


def parse_positive_decimal(raw: str) -> Decimal | None:
    """Parse a positive user-entered decimal without using binary floats."""
    normalized = raw.strip().replace("\u00a0", "").replace(" ", "").replace("_", "")
    if len(normalized) > 32:
        return None
    if "," in normalized and "." in normalized:
        if normalized.rfind(",") > normalized.rfind("."):
            normalized = normalized.replace(".", "").replace(",", ".")
        else:
            normalized = normalized.replace(",", "")
    elif "," in normalized:
        left, right = normalized.rsplit(",", maxsplit=1)
        normalized = left + right if len(right) == 3 and left.isdigit() else left + "." + right
    if not re.fullmatch(r"\+?\d{1,18}(?:\.\d{1,6})?", normalized):
        return None
    try:
        value = Decimal(normalized)
    except InvalidOperation:
        return None
    if not value.is_finite() or value <= 0:
        return None
    return value


def format_decimal(value: Decimal, language: str) -> str:
    """Format a Decimal with readable grouping and language-appropriate decimals."""
    formatted = format(value, ",f")
    whole, separator, fraction = formatted.partition(".")
    whole = whole.replace(",", " ")
    fraction = fraction.rstrip("0")
    if not separator or not fraction:
        return whole
    decimal_separator = "," if language == "ru" else "."
    return f"{whole}{decimal_separator}{fraction}"


@dp.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return
    await state.clear()
    user = ensure_user(message.from_user.id)
    language = user["language"]
    if language:
        await message.answer(
            f"{TEXT[language]['welcome']}\n\n{TEXT[language]['menu']}",
            reply_markup=main_menu(language),
        )
    else:
        await message.answer(TEXT["ru"]["choose"], reply_markup=language_keyboard())


@dp.callback_query(lambda query: query.data in {"lang:ru", "lang:uz"})
async def choose_language(query: CallbackQuery, state: FSMContext) -> None:
    language = query.data.split(":", maxsplit=1)[1]
    await state.clear()
    set_language(query.from_user.id, language)
    await query.answer()
    if query.message:
        await query.message.edit_text(
            f"{TEXT[language]['welcome']}\n\n{TEXT[language]['menu']}",
            reply_markup=main_menu(language),
        )


@dp.callback_query(lambda query: query.data == "menu:language")
async def change_language(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await query.answer()
    if query.message:
        await query.message.edit_text(TEXT["ru"]["choose"], reply_markup=language_keyboard())


@dp.callback_query(lambda query: query.data and query.data.startswith("menu:"))
async def menu_action(query: CallbackQuery, state: FSMContext) -> None:
    language = get_language(query.from_user.id) or "ru"
    await state.clear()
    await query.answer()
    if query.message:
        if query.data == "menu:operation":
            await state.set_state(TransactionFlow.kind)
            await query.message.answer(TEXT[language]["choose_kind"], reply_markup=kind_keyboard(language))
            return
        if query.data == "menu:balance":
            totals = get_balance_totals(query.from_user.id)
            balance_message = TEXT[language]["balance_summary"].format(
                uzs=format_decimal(Decimal(totals["UZS"]), language),
                usd=format_decimal(Decimal(totals["USD"]), language),
            )
            rate = get_usd_to_uzs_rate(query.from_user.id)
            if rate is not None:
                with localcontext() as context:
                    context.prec = 50
                    total_uzs = Decimal(totals["UZS"]) + Decimal(totals["USD"]) * Decimal(rate)
                balance_message += "\n" + TEXT[language]["balance_total"].format(
                    total=format_decimal(total_uzs, language)
                )
            else:
                balance_message += "\n" + TEXT[language]["balance_rate_missing"]
            await query.message.answer(balance_message)
            return
        if query.data == "menu:report":
            report_keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=TEXT[language]["report_week"], callback_data="report:week"),
                InlineKeyboardButton(text=TEXT[language]["report_month"], callback_data="report:month"),
            ]])
            await query.message.answer(TEXT[language]["report_choose"], reply_markup=report_keyboard)
            return
        if query.data == "menu:transactions":
            await show_saved_transactions(query.message, query.from_user.id, language)
            return
        if query.data == "menu:rate":
            current_rate = get_usd_to_uzs_rate(query.from_user.id)
            await state.set_state(TransactionFlow.rate)
            prompt = (
                TEXT[language]["rate_prompt"]
                if current_rate is None
                else TEXT[language]["rate_current"].format(
                    rate=format_decimal(Decimal(current_rate), language)
                )
            )
            await query.message.answer(prompt, reply_markup=rate_keyboard(language))
            return
        await query.message.answer(TEXT[language]["soon"])


@dp.callback_query(lambda query: query.data in {"report:week", "report:month"})
async def export_financial_report(query: CallbackQuery) -> None:
    if not query.message:
        await query.answer()
        return
    language = get_language(query.from_user.id) or "ru"
    if query.message.chat.type != "private":
        await query.answer(TEXT[language]["report_private_only"], show_alert=True)
        return
    today = datetime.now(ZoneInfo("Asia/Tashkent")).date()
    if query.data == "report:week":
        start_date = today - timedelta(days=today.weekday())
        end_date = start_date + timedelta(days=7)
        period = "week"
    else:
        start_date = date_type(today.year, today.month, 1)
        end_date = (
            date_type(today.year + 1, 1, 1)
            if today.month == 12
            else date_type(today.year, today.month + 1, 1)
        )
        period = "month"

    await query.answer()
    try:
        transactions = get_transactions_for_period(query.from_user.id, start_date, end_date)
        if not transactions:
            await query.message.answer(TEXT[language]["report_empty"])
            return
        report_file, filename = await asyncio.to_thread(
            build_financial_report,
            transactions,
            period,
            start_date,
            end_date,
            language,
        )
        await query.message.answer_document(
            BufferedInputFile(report_file.getvalue(), filename=filename)
        )
    except Exception:
        # Never include private transaction fields or internal errors in messages/logs.
        await query.message.answer(TEXT[language]["report_error"])


@dp.callback_query(lambda query: query.data and query.data.startswith("saved:list:"))
async def saved_transactions_page(query: CallbackQuery, state: FSMContext) -> None:
    try:
        offset = int(query.data.rsplit(":", maxsplit=1)[1])
    except (ValueError, AttributeError):
        await query.answer()
        return
    if offset < 0 or offset > 100000 or offset % 10:
        await query.answer()
        return
    await state.clear()
    language = get_language(query.from_user.id) or "ru"
    await query.answer()
    if query.message:
        await show_saved_transactions(query.message, query.from_user.id, language, offset)


@dp.callback_query(lambda query: query.data and query.data.startswith("saved:open:"))
async def open_saved_transaction(query: CallbackQuery, state: FSMContext) -> None:
    try:
        transaction_id = int(query.data.rsplit(":", maxsplit=1)[1])
    except (ValueError, AttributeError):
        await query.answer()
        return
    transaction = get_transaction(query.from_user.id, transaction_id)
    await state.clear()
    language = get_language(query.from_user.id) or "ru"
    await query.answer()
    if not query.message:
        return
    if transaction is None:
        await query.message.answer(TEXT[language]["transaction_not_found"])
        return
    await state.update_data(transaction_id=transaction_id)
    await state.set_state(SavedOperationFlow.viewing)
    await query.message.answer(
        saved_transaction_text(transaction, language),
        reply_markup=transaction_detail_keyboard(transaction_id, language),
    )


@dp.callback_query(SavedOperationFlow.viewing, lambda query: query.data and query.data.startswith("saved:edit:"))
async def begin_saved_transaction_edit(query: CallbackQuery, state: FSMContext) -> None:
    try:
        transaction_id = int(query.data.rsplit(":", maxsplit=1)[1])
    except (ValueError, AttributeError):
        await query.answer()
        return
    view_data = await state.get_data()
    if view_data.get("transaction_id") != transaction_id:
        await query.answer()
        return
    transaction = get_transaction(query.from_user.id, transaction_id)
    await state.clear()
    language = get_language(query.from_user.id) or "ru"
    await query.answer()
    if transaction is None or not query.message:
        if query.message:
            await query.message.answer(TEXT[language]["transaction_not_found"])
        return
    await state.update_data(
        transaction_id=transaction_id,
        original=transaction,
        proposed=dict(transaction),
    )
    await state.set_state(SavedOperationFlow.edit_fields)
    await query.message.answer(
        TEXT[language]["edit_choose_field"],
        reply_markup=edit_fields_keyboard(transaction_id, language),
    )


@dp.callback_query(lambda query: query.data and query.data.startswith("saved:field:"))
async def choose_saved_transaction_field(query: CallbackQuery, state: FSMContext) -> None:
    parts = query.data.split(":")
    if len(parts) != 4:
        await query.answer()
        return
    field, raw_id = parts[2], parts[3]
    try:
        transaction_id = int(raw_id)
    except ValueError:
        await query.answer()
        return
    data = await state.get_data()
    if (await state.get_state()) != SavedOperationFlow.edit_fields.state or data.get("transaction_id") != transaction_id or not data.get("original"):
        await query.answer()
        return
    language = get_language(query.from_user.id) or "ru"
    await query.answer()
    if not query.message:
        return
    if field == "kind":
        await state.set_state(SavedOperationFlow.edit_kind)
        await query.message.answer(
            TEXT[language]["edit_kind"],
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=TEXT[language]["income"], callback_data=f"saved:kind:income:{transaction_id}"),
                InlineKeyboardButton(text=TEXT[language]["expense"], callback_data=f"saved:kind:expense:{transaction_id}"),
            ]]),
        )
    elif field == "currency":
        await state.set_state(SavedOperationFlow.edit_currency)
        await query.message.answer(
            TEXT[language]["choose_currency"],
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="UZS — so‘m", callback_data=f"saved:currency:UZS:{transaction_id}"),
                InlineKeyboardButton(text="USD — $", callback_data=f"saved:currency:USD:{transaction_id}"),
            ]]),
        )
    elif field == "category":
        proposed = data["proposed"]
        await state.set_state(SavedOperationFlow.edit_category)
        await query.message.answer(
            TEXT[language]["choose_category"],
            reply_markup=saved_category_keyboard(language, proposed["kind"], transaction_id),
        )
    elif field in {"amount", "description", "date"}:
        state_map = {
            "amount": (SavedOperationFlow.edit_amount, "enter_new_amount"),
            "description": (SavedOperationFlow.edit_description, "enter_new_description"),
            "date": (SavedOperationFlow.edit_date, "enter_new_date"),
        }
        flow_state, prompt = state_map[field]
        await state.set_state(flow_state)
        await query.message.answer(TEXT[language][prompt])
    else:
        return


def saved_category_keyboard(language: str, kind: str, transaction_id: int) -> InlineKeyboardMarkup:
    labels = TEXT[language]["categories"]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=labels[key], callback_data=f"saved:category:{key}:{transaction_id}")]
        for key in CATEGORIES[kind]
    ])


async def show_edit_review(message: Message, state: FSMContext, language: str) -> None:
    data = await state.get_data()
    proposed = data.get("proposed")
    if not proposed:
        await state.clear()
        return
    await state.set_state(SavedOperationFlow.edit_confirmation)
    await message.answer(
        saved_transaction_text(proposed, language, "edit_review"),
        reply_markup=edit_confirmation_keyboard(language),
    )


@dp.callback_query(SavedOperationFlow.edit_kind, lambda query: query.data and query.data.startswith("saved:kind:"))
async def choose_edited_transaction_kind(query: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("proposed"):
        await query.answer()
        return
    parts = query.data.split(":")
    if len(parts) != 4 or parts[3] != str(data.get("transaction_id")):
        await query.answer()
        return
    kind = parts[2]
    if kind not in {"income", "expense"}:
        await query.answer()
        return
    proposed = data["proposed"]
    proposed["kind"] = kind
    await state.update_data(proposed=proposed)
    language = get_language(query.from_user.id) or "ru"
    await query.answer()
    if proposed["category"] not in CATEGORIES[kind]:
        await state.set_state(SavedOperationFlow.edit_category)
        if query.message:
            await query.message.answer(
                TEXT[language]["choose_category"],
                reply_markup=saved_category_keyboard(language, kind, data["transaction_id"]),
            )
        return
    if query.message:
        await show_edit_review(query.message, state, language)


@dp.callback_query(SavedOperationFlow.edit_category, lambda query: query.data and query.data.startswith("saved:category:"))
async def choose_edited_transaction_category(query: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    proposed = data.get("proposed")
    if not proposed:
        await query.answer()
        return
    parts = query.data.split(":")
    if len(parts) != 4 or parts[3] != str(data.get("transaction_id")):
        await query.answer()
        return
    category = parts[2]
    if category not in CATEGORIES.get(proposed["kind"], []):
        await query.answer()
        return
    proposed["category"] = category
    await state.update_data(proposed=proposed)
    language = get_language(query.from_user.id) or "ru"
    await query.answer()
    if query.message:
        await show_edit_review(query.message, state, language)


@dp.callback_query(SavedOperationFlow.edit_currency, lambda query: query.data and query.data.startswith("saved:currency:"))
async def choose_edited_transaction_currency(query: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    proposed = data.get("proposed")
    if not proposed:
        await query.answer()
        return
    parts = query.data.split(":")
    if len(parts) != 4 or parts[3] != str(data.get("transaction_id")) or parts[2] not in {"UZS", "USD"}:
        await query.answer()
        return
    proposed["currency"] = parts[2]
    await state.update_data(proposed=proposed)
    language = get_language(query.from_user.id) or "ru"
    await query.answer()
    if query.message:
        await show_edit_review(query.message, state, language)


@dp.message(SavedOperationFlow.edit_amount)
async def enter_edited_transaction_amount(message: Message, state: FSMContext) -> None:
    language = get_language(message.from_user.id) if message.from_user else None
    language = language or "ru"
    amount = parse_positive_decimal(message.text or "")
    if amount is None:
        await message.answer(TEXT[language]["invalid_new_amount"])
        return
    data = await state.get_data()
    proposed = data.get("proposed")
    if not proposed:
        await state.clear()
        return
    proposed["amount"] = format(amount, "f")
    await state.update_data(proposed=proposed)
    await show_edit_review(message, state, language)


@dp.message(SavedOperationFlow.edit_description)
async def enter_edited_transaction_description(message: Message, state: FSMContext) -> None:
    language = get_language(message.from_user.id) if message.from_user else None
    language = language or "ru"
    description = message.text or ""
    if len(description) > 1000:
        await message.answer(TEXT[language]["invalid_new_description"])
        return
    data = await state.get_data()
    proposed = data.get("proposed")
    if not proposed:
        await state.clear()
        return
    proposed["description"] = "" if description.strip() == "—" else description.strip()
    await state.update_data(proposed=proposed)
    await show_edit_review(message, state, language)


@dp.message(SavedOperationFlow.edit_date)
async def enter_edited_transaction_date(message: Message, state: FSMContext) -> None:
    language = get_language(message.from_user.id) if message.from_user else None
    language = language or "ru"
    try:
        entered_date = date_type.fromisoformat((message.text or "").strip())
    except ValueError:
        await message.answer(TEXT[language]["invalid_new_date"])
        return
    data = await state.get_data()
    proposed = data.get("proposed")
    if not proposed:
        await state.clear()
        return
    proposed["occurred_at"] = datetime.combine(entered_date, datetime.min.time(), ZoneInfo("Asia/Tashkent")).isoformat(timespec="seconds")
    await state.update_data(proposed=proposed)
    await show_edit_review(message, state, language)


@dp.callback_query(SavedOperationFlow.edit_confirmation, lambda query: query.data == "saved:edit-confirm")
async def confirm_saved_transaction_edit(query: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    original, proposed = data.get("original"), data.get("proposed")
    transaction_id = data.get("transaction_id")
    language = get_language(query.from_user.id) or "ru"
    current = get_transaction(query.from_user.id, transaction_id) if transaction_id else None
    await query.answer()
    if not query.message:
        return
    if not original or not proposed or current != original:
        await state.clear()
        await query.message.answer(TEXT[language]["stale_edit"])
        return
    succeeded = update_transaction(
        query.from_user.id,
        transaction_id,
        kind=proposed["kind"],
        amount=proposed["amount"],
        currency=proposed["currency"],
        category=proposed["category"],
        description=proposed["description"],
        occurred_at=datetime.fromisoformat(proposed["occurred_at"]),
    )
    await state.clear()
    await query.message.answer(TEXT[language]["edit_saved"] if succeeded else TEXT[language]["transaction_not_found"])


@dp.callback_query(SavedOperationFlow.edit_confirmation, lambda query: query.data == "saved:edit-cancel")
async def cancel_saved_transaction_edit(query: CallbackQuery, state: FSMContext) -> None:
    language = get_language(query.from_user.id) or "ru"
    await state.clear()
    await query.answer()
    if query.message:
        await query.message.answer(TEXT[language]["edit_cancelled"])


@dp.callback_query(SavedOperationFlow.viewing, lambda query: query.data and query.data.startswith("saved:delete:"))
async def ask_saved_transaction_delete(query: CallbackQuery, state: FSMContext) -> None:
    try:
        transaction_id = int(query.data.rsplit(":", maxsplit=1)[1])
    except (ValueError, AttributeError):
        await query.answer()
        return
    view_data = await state.get_data()
    if view_data.get("transaction_id") != transaction_id:
        await query.answer()
        return
    transaction = get_transaction(query.from_user.id, transaction_id)
    await state.clear()
    language = get_language(query.from_user.id) or "ru"
    await query.answer()
    if not transaction or not query.message:
        if query.message:
            await query.message.answer(TEXT[language]["transaction_not_found"])
        return
    await state.set_state(SavedOperationFlow.delete_confirmation)
    await state.update_data(transaction_id=transaction_id, original=transaction)
    details = saved_transaction_text(transaction, language)
    await query.message.answer(
        TEXT[language]["delete_review"].format(details=details),
        reply_markup=delete_confirmation_keyboard(language),
    )


@dp.callback_query(SavedOperationFlow.delete_confirmation, lambda query: query.data == "saved:delete-confirm")
async def confirm_saved_transaction_delete(query: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    transaction_id = data.get("transaction_id")
    original = data.get("original")
    language = get_language(query.from_user.id) or "ru"
    current = get_transaction(query.from_user.id, transaction_id) if transaction_id else None
    await query.answer()
    if not query.message:
        return
    if not original or current != original:
        await state.clear()
        await query.message.answer(TEXT[language]["stale_edit"])
        return
    succeeded = delete_transaction(query.from_user.id, transaction_id)
    await state.clear()
    await query.message.answer(TEXT[language]["delete_saved"] if succeeded else TEXT[language]["transaction_not_found"])


@dp.callback_query(SavedOperationFlow.delete_confirmation, lambda query: query.data == "saved:delete-cancel")
async def cancel_saved_transaction_delete(query: CallbackQuery, state: FSMContext) -> None:
    language = get_language(query.from_user.id) or "ru"
    await state.clear()
    await query.answer()
    if query.message:
        await query.message.answer(TEXT[language]["edit_cancelled"])


@dp.message(TransactionFlow.rate)
async def enter_usd_to_uzs_rate(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return
    language = get_language(message.from_user.id) or "ru"
    rate = parse_positive_decimal(message.text or "")
    if rate is None:
        await message.answer(TEXT[language]["rate_invalid"], reply_markup=rate_keyboard(language))
        return
    normalized_rate = format(rate, "f")
    set_usd_to_uzs_rate(message.from_user.id, normalized_rate)
    await state.clear()
    await message.answer(
        TEXT[language]["rate_saved"].format(rate=format_decimal(rate, language))
    )


@dp.callback_query(TransactionFlow.rate, lambda query: query.data == "rate:cancel")
async def cancel_rate_change(query: CallbackQuery, state: FSMContext) -> None:
    language = get_language(query.from_user.id) or "ru"
    await state.clear()
    await query.answer()
    if query.message:
        await query.message.answer(TEXT[language]["rate_cancelled"])


@dp.callback_query(TransactionFlow.kind, lambda query: query.data in {"txkind:income", "txkind:expense"})
async def choose_transaction_kind(query: CallbackQuery, state: FSMContext) -> None:
    kind = query.data.split(":", maxsplit=1)[1]
    await state.update_data(kind=kind)
    await state.set_state(TransactionFlow.amount)
    language = get_language(query.from_user.id) or "ru"
    await query.answer()
    if query.message:
        await query.message.answer(TEXT[language]["enter_amount"])


@dp.message(TransactionFlow.amount)
async def enter_transaction_amount(message: Message, state: FSMContext) -> None:
    language = get_language(message.from_user.id) if message.from_user else None
    language = language or "ru"
    raw = (message.text or "").strip().replace("\u00a0", "").replace(" ", "")
    raw = raw.replace("_", "")
    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        left, right = raw.rsplit(",", maxsplit=1)
        raw = left + right if len(right) == 3 and left.lstrip("+-").isdigit() else left + "." + right
    if len(raw) > 32 or not re.fullmatch(r"\+?\d{1,18}(?:\.\d{1,6})?", raw):
        await message.answer(TEXT[language]["invalid_amount"])
        return
    try:
        amount = Decimal(raw)
        if not amount.is_finite() or amount <= 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        await message.answer(TEXT[language]["invalid_amount"])
        return

    await state.update_data(amount=format(amount, "f"))
    await state.set_state(TransactionFlow.currency)
    await message.answer(TEXT[language]["choose_currency"], reply_markup=currency_keyboard(language))


@dp.callback_query(TransactionFlow.currency, lambda query: query.data in {"txcurrency:UZS", "txcurrency:USD"})
async def choose_transaction_currency(query: CallbackQuery, state: FSMContext) -> None:
    currency = query.data.split(":", maxsplit=1)[1]
    await state.update_data(currency=currency)
    await state.set_state(TransactionFlow.category)
    data = await state.get_data()
    language = get_language(query.from_user.id) or "ru"
    await query.answer()
    if query.message:
        await query.message.answer(
            TEXT[language]["choose_category"],
            reply_markup=category_keyboard(language, data["kind"]),
        )


@dp.callback_query(TransactionFlow.category, lambda query: query.data and query.data.startswith("txcategory:"))
async def choose_transaction_category(query: CallbackQuery, state: FSMContext) -> None:
    category = query.data.split(":", maxsplit=1)[1]
    data = await state.get_data()
    if category not in CATEGORIES.get(data.get("kind"), []):
        await query.answer()
        return
    await state.update_data(category=category)
    await state.set_state(TransactionFlow.description)
    language = get_language(query.from_user.id) or "ru"
    await query.answer()
    if query.message:
        skip_keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=TEXT[language]["skip"], callback_data="tx:skip-description"),
            InlineKeyboardButton(text=TEXT[language]["cancel"], callback_data="tx:cancel"),
        ]])
        await query.message.answer(TEXT[language]["description"], reply_markup=skip_keyboard)


async def show_transaction_confirmation(
    message: Message, state: FSMContext, description: str, telegram_user_id: int
) -> None:
    data = await state.get_data()
    language = get_language(telegram_user_id) or "ru"
    data["description"] = description.strip()
    occurred_at_value = data.get("occurred_at")
    if isinstance(occurred_at_value, str):
        occurred_at = datetime.fromisoformat(occurred_at_value)
    elif isinstance(occurred_at_value, datetime):
        occurred_at = occurred_at_value
    else:
        occurred_at = datetime.now(ZoneInfo("Asia/Tashkent"))
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=ZoneInfo("Asia/Tashkent"))
    data["occurred_at"] = occurred_at.isoformat(timespec="seconds")
    await state.update_data(
        description=data["description"], occurred_at=data["occurred_at"]
    )
    await state.set_state(TransactionFlow.confirmation)
    labels = TEXT[language]
    kind_label = labels[data["kind"]]
    category_label = labels["categories"][data["category"]]
    date_label = occurred_at.astimezone(ZoneInfo("Asia/Tashkent")).strftime("%Y-%m-%d")
    summary = labels["confirmation"].format(
        kind=kind_label,
        amount=data["amount"],
        currency=data["currency"],
        category=category_label,
        description=data["description"] or "—",
        date=date_label,
    )
    await message.answer(summary, reply_markup=transaction_keyboard(language))


@dp.message(TransactionFlow.description)
async def enter_transaction_description(message: Message, state: FSMContext) -> None:
    if message.from_user:
        await show_transaction_confirmation(message, state, message.text or "", message.from_user.id)


@dp.callback_query(TransactionFlow.description, lambda query: query.data == "tx:skip-description")
async def skip_transaction_description(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer()
    if query.message:
        await show_transaction_confirmation(query.message, state, "", query.from_user.id)


@dp.callback_query(TransactionFlow.confirmation, lambda query: query.data == "tx:confirm")
async def confirm_transaction(query: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data or not query.message:
        await query.answer()
        return
    add_transaction(
        telegram_user_id=query.from_user.id,
        kind=data["kind"],
        amount=data["amount"],
        currency=data["currency"],
        category=data["category"],
        description=data.get("description", ""),
        occurred_at=datetime.fromisoformat(data["occurred_at"]),
    )
    language = get_language(query.from_user.id) or "ru"
    await state.clear()
    await query.answer()
    await query.message.answer(TEXT[language]["saved"])


@dp.callback_query(lambda query: query.data == "tx:cancel")
async def cancel_transaction_button(query: CallbackQuery, state: FSMContext) -> None:
    language = get_language(query.from_user.id) or "ru"
    await state.clear()
    await query.answer()
    if query.message:
        await query.message.answer(TEXT[language]["cancelled"])


@dp.message(Command("cancel"))
async def cancel_transaction_command(message: Message, state: FSMContext) -> None:
    language = get_language(message.from_user.id) if message.from_user else None
    language = language or "ru"
    current_state = await state.get_state()
    await state.clear()
    text_key = "rate_cancelled" if current_state == TransactionFlow.rate.state else "cancelled"
    await message.answer(TEXT[language][text_key])


@dp.message(Command("help"))
async def help_command(message: Message) -> None:
    language = get_language(message.from_user.id) if message.from_user else None
    language = language or "ru"
    await message.answer(TEXT[language]["help"])


async def process_financial_text(
    message: Message, state: FSMContext, text: str, language: str
) -> None:
    """Parse text into a draft; persistence still requires user confirmation."""
    if len(text) > 2000:
        await message.answer(TEXT[language]["ai_too_long"])
        return
    if not GROQ_API_KEY:
        await message.answer(TEXT[language]["ai_unconfigured"])
        return
    try:
        parsed = await parse_financial_message(text, language, api_key=GROQ_API_KEY)
    except Exception:
        # Never include the input, API key, or provider exception in logs/messages.
        await message.answer(TEXT[language]["ai_error"])
        return

    if parsed.status == "error":
        await message.answer(TEXT[language]["ai_error"])
        return
    if parsed.status == "unsupported":
        await message.answer(TEXT[language]["ai_unsupported"])
        return
    if parsed.status != "ready":
        field_labels = TEXT[language]["ai_fields"]
        fields = ", ".join(field_labels[field] for field in parsed.missing_fields if field in field_labels)
        await message.answer(
            TEXT[language]["ai_clarify"].format(fields=fields or TEXT[language]["ai_unsupported"])
        )
        return

    await state.clear()
    await state.update_data(
        kind=parsed.kind,
        amount=parsed.amount,
        currency=parsed.currency,
        category=parsed.category,
        description=parsed.description,
        occurred_at=parsed.occurred_at.isoformat(timespec="seconds") if parsed.occurred_at else None,
    )
    await show_transaction_confirmation(message, state, parsed.description, message.from_user.id)


@dp.message(StateFilter(None), lambda message: bool(message.text) and not message.text.startswith("/"))
async def parse_financial_text(message: Message, state: FSMContext) -> None:
    if not message.from_user or not message.text:
        return
    language = get_language(message.from_user.id) or "ru"
    await process_financial_text(message, state, message.text.strip(), language)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    initialize_database()
    bot = Bot(token=BOT_TOKEN)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

