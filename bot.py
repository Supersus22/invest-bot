import os
import asyncio
import logging
import requests
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from flask import Flask
import threading

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
SITE_URL = os.environ.get("SITE_URL", "https://mepon.pythonanywhere.com")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

web = Flask(__name__)

@web.route("/")
def health():
    return "OK"

def run_web():
    port = int(os.environ.get("PORT", 8000))
    web.run(host="0.0.0.0", port=port)


class RegStates(StatesGroup):
    waiting_for_phone = State()


def phone_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Отправить номер", request_contact=True)]],
        resize_keyboard=True
    )


@dp.message(Command("start"))
async def cmd_start(message: types.Message, command: CommandObject, state: FSMContext):
    args = command.args or ""
    if args.startswith("reg_"):
        code = args.replace("reg_", "")
        await state.update_data(reg_code=code)
        await message.answer(
            "🔐 *Регистрация на Invest Portal*\n\n"
            "Нажмите кнопку ниже, чтобы отправить номер — сайт подтвердит вас автоматически.",
            reply_markup=phone_keyboard(), parse_mode="Markdown"
        )
        await state.set_state(RegStates.waiting_for_phone)
        return

    await message.answer(
        "👋 Привет! Это бот Invest Portal.\n\nОтправь номер для регистрации.",
        reply_markup=phone_keyboard()
    )
    await state.set_state(RegStates.waiting_for_phone)


@dp.message(RegStates.waiting_for_phone, F.contact)
async def process_contact(message: types.Message, state: FSMContext):
    phone = message.contact.phone_number
    tg = message.from_user.username or ""
    name = message.from_user.full_name
    data = await state.get_data()
    code = data.get("reg_code")

    if code:
        try:
            r = requests.post(f"{SITE_URL}/api/verify_telegram",
                              json={"code": code, "phone": phone,
                                    "telegram": "@" + tg, "full_name": name},
                              timeout=10)
            if r.status_code == 200:
                await message.answer("✅ Готово! Вернитесь на сайт.", parse_mode="Markdown")
            else:
                await message.answer(f"⚠️ Ошибка сайта: {r.status_code}")
        except Exception as e:
            await message.answer(f"⚠️ Ошибка: {e}")
    else:
        await message.answer("✅ Номер сохранён.")
    await state.clear()


@dp.message(RegStates.waiting_for_phone)
async def wrong(message: types.Message):
    await message.answer("⚠️ Нажмите кнопку «📱 Отправить номер».")


async def main():
    print("🚀 Бот запущен!")
    threading.Thread(target=run_web, daemon=True).start()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
