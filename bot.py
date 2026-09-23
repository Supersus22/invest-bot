import asyncio
import logging
import sqlite3
import re
import random
import requests
import os
import threading
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from flask import Flask

logging.basicConfig(level=logging.INFO)

# ===== КОНФИГ =====
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_IDS = [7643451177]
OWNER_USERNAME = "Администратор"
SITE_URL = os.environ.get("SITE_URL", "https://mepon.pythonanywhere.com")
TELEGRAPH_URL = "https://telegra.ph/Politika-konfidencialnosti-09-08-92"

EQN_BASE_PRICE = 45
EQN_PRICE_STEP = 5
EQN_GROUP_SIZE = 10
ANE4K_BASE_PRICE = 45
ANE4K_PRICE_STEP = 5
ANE4K_GROUP_SIZE = 10

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ===== МИНИ FLASK ДЛЯ RENDER =====
web = Flask(__name__)

@web.route("/")
def health():
    return "OK — bot is running"

def run_web():
    port = int(os.environ.get("PORT", 8000))
    web.run(host="0.0.0.0", port=port)


# ===== БАЗА =====
def init_db():
    conn = sqlite3.connect('investments.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, phone TEXT, username TEXT, full_name TEXT,
        registered_at TEXT, balance INTEGER DEFAULT 0, is_banned INTEGER DEFAULT 0,
        honor_activated INTEGER DEFAULT 0, debt INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS stocks_eqn (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, quantity INTEGER,
        price_per_unit INTEGER, purchased_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS stocks_ane4k (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, quantity INTEGER,
        price_per_unit INTEGER, purchased_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, type TEXT,
        amount INTEGER, description TEXT, created_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS support_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT,
        phone TEXT, message TEXT, created_at TEXT, is_read INTEGER DEFAULT 0,
        is_replied INTEGER DEFAULT 0, admin_reply TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS profits (
        id INTEGER PRIMARY KEY AUTOINCREMENT, from_user_id INTEGER, amount INTEGER,
        created_at TEXT, description TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS pending_deposits (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount INTEGER,
        created_at TEXT, status TEXT DEFAULT 'pending')''')
    c.execute('''CREATE TABLE IF NOT EXISTS config (
        key TEXT PRIMARY KEY, value TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS diplomas (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, reason TEXT,
        awarded_at TEXT, expires_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS honor_investments (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, code TEXT UNIQUE,
        awarded_at TEXT, expires_at TEXT, is_sold INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS temporary_titles (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, title TEXT,
        reason TEXT, awarded_at TEXT, expires_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS honor_activations (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, activated_at TEXT)''')

    c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('eqn_base_price', ?)", (str(EQN_BASE_PRICE),))
    c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('eqn_price_step', ?)", (str(EQN_PRICE_STEP),))
    c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('eqn_group_size', ?)", (str(EQN_GROUP_SIZE),))
    c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('ane4k_base_price', ?)", (str(ANE4K_BASE_PRICE),))
    c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('ane4k_price_step', ?)", (str(ANE4K_PRICE_STEP),))
    c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('ane4k_group_size', ?)", (str(ANE4K_GROUP_SIZE),))
    conn.commit()
    conn.close()

init_db()


# ===== КОНФИГ =====
def get_config(key):
    conn = sqlite3.connect('investments.db')
    c = conn.cursor()
    c.execute("SELECT value FROM config WHERE key = ?", (key,))
    r = c.fetchone()
    conn.close()
    return int(r[0]) if r else None

def set_config(key, value):
    conn = sqlite3.connect('investments.db')
    c = conn.cursor()
    c.execute("UPDATE config SET value = ? WHERE key = ?", (str(value), key))
    conn.commit()
    conn.close()

def get_eqn_base_price(): return get_config('eqn_base_price') or 45
def get_eqn_price_step(): return get_config('eqn_price_step') or 5
def get_eqn_group_size(): return get_config('eqn_group_size') or 10
def get_ane4k_base_price(): return get_config('ane4k_base_price') or 45
def get_ane4k_price_step(): return get_config('ane4k_price_step') or 5
def get_ane4k_group_size(): return get_config('ane4k_group_size') or 10


def get_stock_price(user_id, stock_type):
    conn = sqlite3.connect('investments.db')
    c = conn.cursor()
    table = "stocks_eqn" if stock_type == "EQN" else "stocks_ane4k"
    base = get_eqn_base_price() if stock_type == "EQN" else get_ane4k_base_price()
    step = get_eqn_price_step() if stock_type == "EQN" else get_ane4k_price_step()
    group = get_eqn_group_size() if stock_type == "EQN" else get_ane4k_group_size()
    c.execute(f"SELECT SUM(quantity) FROM {table} WHERE user_id = ?", (user_id,))
    total = c.fetchone()[0] or 0
    conn.close()
    return base + (total // group) * step


def get_stock_percent(user_id, stock_type):
    cur = get_stock_price(user_id, stock_type)
    base = get_eqn_base_price() if stock_type == "EQN" else get_ane4k_base_price()
    return round(((cur - base) / base) * 100, 1)


# ===== ПОЛЬЗОВАТЕЛИ =====
def get_user(user_id):
    conn = sqlite3.connect('investments.db')
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    r = c.fetchone()
    conn.close()
    return r

def get_user_by_phone(phone):
    conn = sqlite3.connect('investments.db')
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE phone = ?", (phone,))
    r = c.fetchone()
    conn.close()
    return r

def create_user(user_id, phone, username=None, full_name=None):
    conn = sqlite3.connect('investments.db')
    c = conn.cursor()
    c.execute('''INSERT INTO users (user_id, phone, username, full_name, registered_at)
                 VALUES (?, ?, ?, ?, ?)''',
              (user_id, phone, username, full_name, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect('investments.db')
    c = conn.cursor()
    c.execute("SELECT user_id, phone, username, full_name, balance, registered_at, honor_activated, debt FROM users WHERE is_banned = 0")
    r = c.fetchall()
    conn.close()
    return r

def get_user_stocks(user_id, table):
    conn = sqlite3.connect('investments.db')
    c = conn.cursor()
    c.execute(f"SELECT quantity, price_per_unit FROM {table} WHERE user_id = ?", (user_id,))
    r = c.fetchall()
    conn.close()
    return r


# ===== ОПЕРАЦИИ =====
def deposit_balance(user_id, amount):
    conn = sqlite3.connect('investments.db')
    c = conn.cursor()
    c.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
    c.execute("""INSERT INTO transactions (user_id, type, amount, description, created_at)
                 VALUES (?, 'deposit', ?, ?, ?)""",
              (user_id, amount, f"Пополнение {amount}", datetime.now().isoformat()))
    conn.commit()
    conn.close()

def withdraw_balance(user_id, amount):
    conn = sqlite3.connect('investments.db')
    c = conn.cursor()
    c.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, user_id))
    c.execute("""INSERT INTO transactions (user_id, type, amount, description, created_at)
                 VALUES (?, 'withdraw', ?, ?, ?)""",
              (user_id, amount, f"Списание {amount} (админ)", datetime.now().isoformat()))
    conn.commit()
    conn.close()

def add_debt(user_id, amount):
    conn = sqlite3.connect('investments.db')
    c = conn.cursor()
    c.execute("UPDATE users SET debt = debt + ? WHERE user_id = ?", (amount, user_id))
    c.execute("""INSERT INTO transactions (user_id, type, amount, description, created_at)
                 VALUES (?, 'debt', ?, ?, ?)""",
              (user_id, amount, f"Долг {amount}", datetime.now().isoformat()))
    conn.commit()
    conn.close()

def add_profit(from_user_id, amount, description="Пополнение"):
    conn = sqlite3.connect('investments.db')
    c = conn.cursor()
    c.execute("""INSERT INTO profits (from_user_id, amount, created_at, description)
                 VALUES (?, ?, ?, ?)""",
              (from_user_id, amount, datetime.now().isoformat(), description))
    conn.commit()
    conn.close()

def get_total_profit():
    conn = sqlite3.connect('investments.db')
    c = conn.cursor()
    c.execute("SELECT SUM(amount) FROM profits")
    r = c.fetchone()[0]
    conn.close()
    return r or 0


def purchase_stock(user_id, stock_type, quantity):
    conn = sqlite3.connect('investments.db')
    c = conn.cursor()
    price = get_stock_price(user_id, stock_type)
    total = quantity * price
    table = "stocks_eqn" if stock_type == "EQN" else "stocks_ane4k"
    c.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    balance = c.fetchone()[0]
    if balance < total:
        conn.close()
        return False, "Недостаточно средств!"
    c.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (total, user_id))
    c.execute(f"""INSERT INTO {table} (user_id, quantity, price_per_unit, purchased_at)
                  VALUES (?, ?, ?, ?)""",
              (user_id, quantity, price, datetime.now().isoformat()))
    c.execute("""INSERT INTO transactions (user_id, type, amount, description, created_at)
                 VALUES (?, 'purchase', ?, ?, ?)""",
              (user_id, total, f"Покупка {stock_type} x{quantity} по {price}", datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return True, f"{stock_type} x{quantity} куплены по {price} ⭐"


def sell_stock(user_id, stock_type, quantity):
    conn = sqlite3.connect('investments.db')
    c = conn.cursor()
    price = get_stock_price(user_id, stock_type)
    total = quantity * price
    table = "stocks_eqn" if stock_type == "EQN" else "stocks_ane4k"
    c.execute(f"SELECT SUM(quantity) FROM {table} WHERE user_id = ?", (user_id,))
    total_q = c.fetchone()[0] or 0
    if total_q < quantity:
        conn.close()
        return False, f"У вас только {total_q} акций {stock_type}"
    c.execute(f"DELETE FROM {table} WHERE user_id = ? AND id IN (SELECT id FROM {table} WHERE user_id = ? LIMIT ?)",
              (user_id, user_id, quantity))
    c.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (total, user_id))
    c.execute("""INSERT INTO transactions (user_id, type, amount, description, created_at)
                 VALUES (?, 'sell', ?, ?, ?)""",
              (user_id, total, f"Продажа {stock_type} x{quantity} по {price}", datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return True, f"{stock_type} x{quantity} проданы по {price} ⭐"


def get_user_transactions(user_id):
    conn = sqlite3.connect('investments.db')
    c = conn.cursor()
    c.execute("SELECT type, amount, description, created_at FROM transactions WHERE user_id = ? ORDER BY created_at DESC LIMIT 20", (user_id,))
    r = c.fetchall()
    conn.close()
    return r


# ===== ПОДДЕРЖКА =====
def save_support_message(user_id, username, phone, message):
    conn = sqlite3.connect('investments.db')
    c = conn.cursor()
    c.execute("""INSERT INTO support_messages (user_id, username, phone, message, created_at, is_replied)
                 VALUES (?, ?, ?, ?, ?, 0)""",
              (user_id, username, phone, message, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_unread_support():
    conn = sqlite3.connect('investments.db')
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM support_messages WHERE is_read = 0")
    r = c.fetchone()[0]
    conn.close()
    return r


# ===== ДЕПОЗИТЫ =====
def create_pending_deposit(user_id, amount):
    conn = sqlite3.connect('investments.db')
    c = conn.cursor()
    c.execute("""INSERT INTO pending_deposits (user_id, amount, created_at, status)
                 VALUES (?, ?, ?, 'pending')""",
              (user_id, amount, datetime.now().isoformat()))
    conn.commit()
    did = c.lastrowid
    conn.close()
    return did

def get_pending_deposit(deposit_id):
    conn = sqlite3.connect('investments.db')
    c = conn.cursor()
    c.execute("SELECT * FROM pending_deposits WHERE id = ?", (deposit_id,))
    r = c.fetchone()
    conn.close()
    return r

def update_pending_deposit(deposit_id, status):
    conn = sqlite3.connect('investments.db')
    c = conn.cursor()
    c.execute("UPDATE pending_deposits SET status = ? WHERE id = ?", (status, deposit_id))
    conn.commit()
    conn.close()

def get_pending_deposits():
    conn = sqlite3.connect('investments.db')
    c = conn.cursor()
    c.execute("SELECT * FROM pending_deposits WHERE status = 'pending' ORDER BY created_at DESC")
    r = c.fetchall()
    conn.close()
    return r


def get_debtors():
    conn = sqlite3.connect('investments.db')
    c = conn.cursor()
    c.execute("SELECT user_id, full_name, phone, balance, debt FROM users WHERE debt > 0 ORDER BY debt DESC")
    r = c.fetchall()
    conn.close()
    return r


# ===== FSM =====
class RegistrationStates(StatesGroup):
    waiting_for_phone = State()

class PurchaseStates(StatesGroup):
    selecting_quantity = State()
    selecting_sell_quantity = State()

class AdminStates(StatesGroup):
    giving_stock = State()
    giving_quantity = State()
    mass_mailing = State()
    changing_price_value = State()
    waiting_for_reply = State()
    add_debt = State()
    withdraw_stars = State()
    award_diploma = State()
    award_honor = State()
    award_title = State()

class SupportStates(StatesGroup):
    waiting_for_message = State()


# ===== КЛАВИАТУРЫ =====
def main_keyboard():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📊 Мой портфель")],
        [KeyboardButton(text="💰 Баланс"), KeyboardButton(text="🛒 Купить акции")],
        [KeyboardButton(text="📈 История"), KeyboardButton(text="👤 Профиль")],
        [KeyboardButton(text="📩 Поддержка")],
    ], resize_keyboard=True)

def deposit_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ 45", callback_data="deposit_45")],
        [InlineKeyboardButton(text="⭐ 90", callback_data="deposit_90")],
        [InlineKeyboardButton(text="⭐ 150", callback_data="deposit_150")],
    ])

def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="💰 Прибыль", callback_data="admin_profit")],
        [InlineKeyboardButton(text="📩 Обращения", callback_data="admin_support")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_mailing")],
        [InlineKeyboardButton(text="⚙️ Цены", callback_data="admin_prices")],
        [InlineKeyboardButton(text="💀 Должники", callback_data="admin_debtors")],
        [InlineKeyboardButton(text="➕ Добавить долг", callback_data="admin_add_debt")],
        [InlineKeyboardButton(text="➖ Забрать звёзды", callback_data="admin_withdraw")],
    ])

def phone_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Отправить номер", request_contact=True)]],
        resize_keyboard=True)


def get_consent_text():
    return f"""📋 Политика конфиденциальности

Отправляя номер, вы принимаете условия:
🔗 {TELEGRAPH_URL}
"""


# ===== /start =====
@dp.message(Command("start"))
async def cmd_start(message: types.Message, command: CommandObject, state: FSMContext):
    user_id = message.from_user.id
    args = command.args or ""

    if args.startswith("reg_"):
        await state.update_data(reg_code=args)
        await message.answer(
            "🔐 *Регистрация на сайте Invest Portal*\n\n"
            "Нажмите кнопку ниже, чтобы отправить номер.",
            reply_markup=phone_keyboard(), parse_mode="Markdown"
        )
        await state.set_state(RegistrationStates.waiting_for_phone)
        return

    user = get_user(user_id)
    if user:
        await message.answer(f"👋 С возвращением, {message.from_user.first_name}!", reply_markup=main_keyboard())
        return

    await message.answer(
        f"{get_consent_text()}\n\nНажмите кнопку, чтобы отправить номер:",
        reply_markup=phone_keyboard()
    )
    await state.set_state(RegistrationStates.waiting_for_phone)


@dp.message(RegistrationStates.waiting_for_phone)
async def process_contact(message: types.Message, state: FSMContext):
    contact = message.contact
    if not contact:
        await message.answer("❌ Используйте кнопку «📱 Отправить номер».")
        return

    user_id = message.from_user.id
    phone = contact.phone_number

    existing = get_user(user_id)
    if not existing:
        existing_phone = get_user_by_phone(phone)
        if existing_phone:
            await message.answer("❌ Этот номер уже зарегистрирован.", reply_markup=main_keyboard())
            await state.clear()
            return
        create_user(user_id, phone, message.from_user.username, message.from_user.full_name)
        await message.answer(f"✅ Регистрация завершена!\n📱 {phone}", reply_markup=main_keyboard())
    else:
        await message.answer("✅ Вы уже зарегистрированы.", reply_markup=main_keyboard())

    data = await state.get_data()
    reg_code = data.get("reg_code")
    if reg_code:
        try:
            requests.post(f"{SITE_URL}/api/verify_telegram",
                          json={
                              "code": reg_code,
                              "phone": phone,
                              "telegram": "@" + (message.from_user.username or ""),
                              "full_name": message.from_user.full_name
                          }, timeout=10)
            await message.answer("✅ Сайт подтвердил вас! Вернитесь и обновите страницу.")
        except Exception as e:
            await message.answer(f"⚠️ Ошибка связи с сайтом: {e}")

    await state.clear()


# ===== БАЛАНС =====
@dp.message(F.text == "💰 Баланс")
async def show_balance(message: types.Message):
    user = get_user(message.from_user.id)
    if not user:
        await message.answer("❌ Сначала /start")
        return
    debt = user[8] or 0
    txt = f"💰 *Баланс:* {user[5]} ⭐"
    if debt > 0:
        txt += f"\n⚠️ *Долг:* {debt} ⭐"
    await message.answer(txt, reply_markup=deposit_keyboard(), parse_mode="Markdown")


@dp.callback_query(lambda c: c.data.startswith("deposit_"))
async def process_deposit(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    amount = int(callback.data.split("_")[1])
    did = create_pending_deposit(user_id, amount)
    user = get_user(user_id)
    for admin_id in ADMIN_IDS:
        try:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"approve_{did}")],
                [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{did}")],
            ])
            await bot.send_message(admin_id,
                f"💳 Заявка на пополнение\n👤 {user[3]}\n🆔 {user_id}\n📱 {user[1]}\n💰 {amount} ⭐",
                reply_markup=kb)
        except:
            pass
    await callback.message.edit_text(f"✅ Заявка на {amount} ⭐ отправлена.")


@dp.callback_query(lambda c: c.data.startswith("approve_"))
async def approve_deposit(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔")
        return
    await callback.answer()
    did = int(callback.data.split("_")[1])
    d = get_pending_deposit(did)
    if not d or d[4] != "pending":
        await callback.message.edit_text("❌ Уже обработано")
        return
    deposit_balance(d[1], d[2])
    add_profit(d[1], d[2], "Пополнение")
    update_pending_deposit(did, "approved")
    await callback.message.edit_text(f"✅ Заявка #{did} одобрена на {d[2]} ⭐")
    try:
        await bot.send_message(d[1], f"✅ Баланс пополнен на {d[2]} ⭐")
    except:
        pass


@dp.callback_query(lambda c: c.data.startswith("reject_"))
async def reject_deposit(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔")
        return
    await callback.answer()
    did = int(callback.data.split("_")[1])
    update_pending_deposit(did, "rejected")
    await callback.message.edit_text(f"❌ Заявка #{did} отклонена")


# ===== ПОРТФЕЛЬ =====
@dp.message(F.text == "📊 Мой портфель")
async def show_portfolio(message: types.Message):
    user_id = message.from_user.id
    user = get_user(user_id)
    if not user:
        await message.answer("❌ Сначала /start")
        return
    eqn = get_user_stocks(user_id, "stocks_eqn")
    ane4k = get_user_stocks(user_id, "stocks_ane4k")
    t_eqn = sum(s[0] for s in eqn) if eqn else 0
    t_ane4k = sum(s[0] for s in ane4k) if ane4k else 0
    eqn_p = get_stock_price(user_id, "EQN")
    ane4k_p = get_stock_price(user_id, "ANE4K")
    eqn_per = get_stock_percent(user_id, "EQN")
    ane4k_per = get_stock_percent(user_id, "ANE4K")
    txt = (f"📊 *Ваш портфель*\n\n"
           f"📈 EQN: {t_eqn} шт. по {eqn_p} ⭐ ({eqn_per}%)\n"
           f"🌟 ANE4K: {t_ane4k} шт. по {ane4k_p} ⭐ ({ane4k_per}%)")
    await message.answer(txt, parse_mode="Markdown")


# ===== КУПИТЬ =====
@dp.message(F.text == "🛒 Купить акции")
async def buy_menu(message: types.Message):
    user_id = message.from_user.id
    user = get_user(user_id)
    if not user:
        await message.answer("❌ Сначала /start")
        return
    eqn = get_stock_price(user_id, "EQN")
    ane4k = get_stock_price(user_id, "ANE4K")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📈 EQN ({eqn} ⭐)", callback_data="buy_EQN")],
        [InlineKeyboardButton(text=f"🌟 ANE4K ({ane4k} ⭐)", callback_data="buy_ANE4K")],
    ])
    await message.answer("🛒 Что купить?", reply_markup=kb)


@dp.callback_query(lambda c: c.data.startswith("buy_"))
async def process_buy(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    st = callback.data.split("_")[1]
    await state.update_data(buy_stock=st)
    await callback.message.edit_text(f"📈 {st}\nВведите количество (1-100):")
    await state.set_state(PurchaseStates.selecting_quantity)


@dp.message(PurchaseStates.selecting_quantity)
async def process_buy_qty(message: types.Message, state: FSMContext):
    try:
        qty = int(message.text.strip())
        if qty < 1 or qty > 100:
            raise ValueError
    except:
        await message.answer("❌ Введите число от 1 до 100")
        return
    data = await state.get_data()
    st = data.get("buy_stock")
    ok, res = purchase_stock(message.from_user.id, st, qty)
    await message.answer(("✅ " if ok else "❌ ") + res)
    await state.clear()


# ===== ИСТОРИЯ =====
@dp.message(F.text == "📈 История")
async def show_history(message: types.Message):
    user_id = message.from_user.id
    trans = get_user_transactions(user_id)
    if not trans:
        await message.answer("📭 Нет транзакций")
        return
    txt = "📈 *История*\n\n"
    for t, amt, desc, _ in trans:
        txt += f"• {desc}: {amt} ⭐\n"
    await message.answer(txt, parse_mode="Markdown")


# ===== ПРОФИЛЬ =====
@dp.message(F.text == "👤 Профиль")
async def show_profile(message: types.Message):
    user = get_user(message.from_user.id)
    if not user:
        await message.answer("❌ Сначала /start")
        return
    txt = (f"👤 *Профиль*\n\n"
           f"🆔 {user[0]}\n"
           f"Имя: {user[3]}\n"
           f"📱 {user[1]}\n"
           f"💰 Баланс: {user[5]} ⭐\n"
           f"💀 Долг: {user[8]} ⭐\n\n"
           f"👩‍💻 Админ: {OWNER_USERNAME}")
    await message.answer(txt, parse_mode="Markdown")


# ===== ПОДДЕРЖКА =====
@dp.message(F.text == "📩 Поддержка")
async def support_menu(message: types.Message, state: FSMContext):
    await message.answer("📩 Напишите ваше сообщение:")
    await state.set_state(SupportStates.waiting_for_message)


@dp.message(SupportStates.waiting_for_message)
async def process_support(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    user = get_user(user_id)
    if not user:
        await message.answer("❌ Сначала /start")
        await state.clear()
        return
    save_support_message(user_id, user[2] or user[3], user[1], message.text)
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id,
                f"📩 Новое обращение от {user[3]} ({user_id})\n📱 {user[1]}\n\n{message.text}")
        except:
            pass
    await message.answer("✅ Отправлено админу.")
    await state.clear()


# ===== АДМИН =====
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ Доступ запрещён")
        return
    unread = get_unread_support()
    profit = get_total_profit()
    debtors = get_debtors()
    await message.answer(
        f"🔐 *Админ-панель*\n📩 Непрочитанных: {unread}\n💰 Прибыль: {profit} ⭐\n💀 Должников: {len(debtors)}",
        reply_markup=admin_keyboard(), parse_mode="Markdown"
    )


@dp.callback_query(lambda c: c.data.startswith("admin_"))
async def admin_cb(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔")
        return
    await callback.answer()
    d = callback.data
    if d == "admin_users":
        users = get_all_users()
        txt = "👥 *Пользователи*\n\n"
        for u in users[:50]:
            txt += f"🆔 {u[0]} | {u[3]} | 📱 {u[1]} | {u[4]} ⭐\n"
        await callback.message.edit_text(txt or "Нет пользователей", reply_markup=admin_keyboard(), parse_mode="Markdown")
    elif d == "admin_stats":
        users = get_all_users()
        txt = (f"📊 *Статистика*\n\n"
               f"👥 Пользователей: {len(users)}\n"
               f"💰 Баланс: {sum(u[4] for u in users)} ⭐\n"
               f"💰 Прибыль: {get_total_profit()} ⭐\n"
               f"💀 Должников: {len(get_debtors())}")
        await callback.message.edit_text(txt, reply_markup=admin_keyboard(), parse_mode="Markdown")
    elif d == "admin_profit":
        conn = sqlite3.connect('investments.db')
        c = conn.cursor()
        c.execute("SELECT u.full_name, p.amount, p.description FROM profits p LEFT JOIN users u ON p.from_user_id = u.user_id ORDER BY p.created_at DESC LIMIT 20")
        rows = c.fetchall()
        conn.close()
        txt = "💰 *Прибыль*\n\n" + "\n".join([f"• {r[0] or '?'}: +{r[1]} ⭐ ({r[2]})" for r in rows])
        await callback.message.edit_text(txt or "Нет данных", reply_markup=admin_keyboard(), parse_mode="Markdown")
    elif d == "admin_debtors":
        ds = get_debtors()
        txt = "💀 *Должники*\n\n" + "\n".join([f"🆔 {d[0]} | {d[1]} | 📱 {d[2]} | долг: {d[4]} ⭐" for d in ds])
        await callback.message.edit_text(txt or "Нет должников", reply_markup=admin_keyboard(), parse_mode="Markdown")
    elif d == "admin_support":
        conn = sqlite3.connect('investments.db')
        c = conn.cursor()
        c.execute("SELECT user_id, username, phone, message, created_at FROM support_messages ORDER BY created_at DESC LIMIT 10")
        rows = c.fetchall()
        conn.close()
        txt = "📩 *Обращения*\n\n" + "\n".join([f"🆔 {r[0]} | {r[1]} | 📱 {r[2]}\n   {r[3][:60]}..." for r in rows])
        await callback.message.edit_text(txt or "Нет обращений", reply_markup=admin_keyboard(), parse_mode="Markdown")
    elif d == "admin_prices":
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"📈 EQN база ({get_eqn_base_price()} ⭐)", callback_data="price_eqn_base")],
            [InlineKeyboardButton(text=f"🌟 ANE4K база ({get_ane4k_base_price()} ⭐)", callback_data="price_ane4k_base")],
        ])
        await callback.message.edit_text("⚙️ Цены:", reply_markup=kb)
    elif d == "admin_mailing":
        await callback.message.edit_text("📢 Введите текст рассылки:")
        await state.set_state(AdminStates.mass_mailing)
    elif d == "admin_add_debt":
        await callback.message.edit_text("➕ Введите ID и сумму через пробел:")
        await state.set_state(AdminStates.add_debt)
    elif d == "admin_withdraw":
        await callback.message.edit_text("➖ Введите ID и сумму через пробел:")
        await state.set_state(AdminStates.withdraw_stars)


@dp.callback_query(lambda c: c.data.startswith("price_"))
async def price_select(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔")
        return
    await callback.answer()
    pt = callback.data.replace("price_", "")
    await state.update_data(price_type=pt)
    await callback.message.edit_text("Введите новое значение:")
    await state.set_state(AdminStates.changing_price_value)


@dp.message(AdminStates.changing_price_value)
async def change_price(message: types.Message, state: FSMContext):
    try:
        v = int(message.text.strip())
        if v < 0:
            raise ValueError
    except:
        await message.answer("❌ Введите число")
        return
    data = await state.get_data()
    pt = data.get("price_type")
    if pt == "eqn_base":
        set_config("eqn_base_price", v)
    elif pt == "ane4k_base":
        set_config("ane4k_base_price", v)
    await message.answer(f"✅ Изменено на {v}")
    await state.clear()


@dp.message(AdminStates.add_debt)
async def adm_add_debt(message: types.Message, state: FSMContext):
    parts = message.text.strip().split()
    if len(parts) != 2:
        await message.answer("❌ Введите ID и сумму")
        return
    try:
        uid, amt = int(parts[0]), int(parts[1])
    except:
        await message.answer("❌ Числа")
        return
    add_debt(uid, amt)
    u = get_user(uid)
    await message.answer(f"✅ Долг {amt} ⭐ добавлен {u[3]}")
    await state.clear()


@dp.message(AdminStates.withdraw_stars)
async def adm_withdraw(message: types.Message, state: FSMContext):
    parts = message.text.strip().split()
    if len(parts) != 2:
        await message.answer("❌ Введите ID и сумму")
        return
    try:
        uid, amt = int(parts[0]), int(parts[1])
    except:
        await message.answer("❌ Числа")
        return
    withdraw_balance(uid, amt)
    await message.answer(f"✅ Списано {amt} ⭐ у #{uid}")
    await state.clear()


@dp.message(AdminStates.mass_mailing)
async def adm_mailing(message: types.Message, state: FSMContext):
    users = get_all_users()
    sent = 0
    for u in users:
        try:
            await bot.send_message(u[0], f"📢 {message.text}")
            sent += 1
        except:
            pass
        await asyncio.sleep(0.05)
    await message.answer(f"✅ Отправлено: {sent}")
    await state.clear()


@dp.message(AdminStates.waiting_for_reply)
async def adm_reply(message: types.Message, state: FSMContext):
    data = await state.get_data()
    uid = data.get("reply_user_id")
    try:
        await bot.send_message(uid, f"📩 Ответ админа:\n\n{message.text}")
        await message.answer("✅ Отправлено")
    except:
        await message.answer("❌ Ошибка")
    await state.clear()


# ===== ЗАПУСК =====
async def main():
    print("🚀 Бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()  # Flask СНАЧАЛА
    asyncio.run(main())                                     # потом бот
