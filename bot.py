"""Entry point for the AI Finance Assistant Telegram bot."""

import asyncio
import logging
import os
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv

from database import (
    add_transaction,
    ensure_user,
    get_balance_totals,
    get_language,
    get_usd_to_uzs_rate,
    initialize_database,
    set_language,
    set_usd_to_uzs_rate,
)


load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

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
        "stale": "Этот шаг уже завершён. Начните заново через меню.",
        "balance_summary": "Баланс:\nUZS: {uzs}\nUSD: {usd}",
        "balance_total": "Общий эквивалент по заданному курсу: {total} UZS",
        "balance_rate_missing": "Курс USD/UZS не задан. Его можно установить кнопкой «Курс USD/UZS».",
        "rate_prompt": "Введите, сколько сум приходится на 1 USD (например, 12900 или 12900,50).",
        "rate_current": "Текущий курс: 1 USD = {rate} UZS.\nВведите новый курс или отмените действие.",
        "rate_invalid": "Введите положительный курс числом, например 12900 или 12900,50.",
        "rate_saved": "Курс сохранён: 1 USD = {rate} UZS.",
        "rate_cancelled": "Изменение курса отменено.",
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
        "stale": "Bu qadam allaqachon yakunlangan. Menyu orqali qaytadan boshlang.",
        "balance_summary": "Balans:\nUZS: {uzs}\nUSD: {usd}",
        "balance_total": "Belgilangan kurs bo‘yicha jami: {total} UZS",
        "balance_rate_missing": "USD/UZS kursi belgilanmagan. Uni «USD/UZS kursi» tugmasi orqali o‘rnating.",
        "rate_prompt": "1 USD necha so‘m ekanini kiriting (masalan, 12900 yoki 12900,50).",
        "rate_current": "Joriy kurs: 1 USD = {rate} UZS.\nYangi kursni kiriting yoki amalni bekor qiling.",
        "rate_invalid": "Musbat kursni kiriting, masalan 12900 yoki 12900,50.",
        "rate_saved": "Kurs saqlandi: 1 USD = {rate} UZS.",
        "rate_cancelled": "Kursni o‘zgartirish bekor qilindi.",
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
    await state.update_data(description=data["description"])
    await state.set_state(TransactionFlow.confirmation)
    labels = TEXT[language]
    kind_label = labels[data["kind"]]
    category_label = labels["categories"][data["category"]]
    date_label = datetime.now(ZoneInfo("Asia/Tashkent")).strftime("%Y-%m-%d")
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
        occurred_at=datetime.now(ZoneInfo("Asia/Tashkent")),
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


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    initialize_database()
    bot = Bot(token=BOT_TOKEN)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

