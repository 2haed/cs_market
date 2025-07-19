import asyncio
import math
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery
from config import TOKEN, DB_CONFIG, CS_KNIVES_LC, CS_GLOVES_LC
from main import parse_items, get_top_10_rating_items, adjust_prices, save_user_watch_items
from typing import Optional, List, Dict
import psycopg2
import asyncio
import logging
from aiogram.fsm.state import StatesGroup, State
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import CommandStart, Command
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime
from aiogram import Router, types, F
from aiogram.fsm.state import State, StatesGroup


bot = Bot(token=TOKEN)
dp = Dispatcher()
router = Router()


user_states = {}
user_selected_subtypes = {}

class WatchItemStates(StatesGroup):
    waiting_for_item_text = State()

# --- Фоновая задача для парсинга every 5 minutes ---
def schedule_parsing():
    result = parse_items("both")
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Scheduled parsing result:\n{result}")

def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Парсить предметы", callback_data="menu_parse")],
        [InlineKeyboardButton(text="📊 Топ-10 по рейтингу", callback_data="menu_top10")],
        [InlineKeyboardButton(text="♻️ Обновить предметы на продаже", callback_data="adjust_prices")],
        [InlineKeyboardButton(text="💾 Сохранить предметы", callback_data="save_items")]  # Кнопка "Сохранить предметы"
    ])

# --- Меню выбора типа предмета ---
def type_selector(prefix: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔪 Knife", callback_data=f"{prefix}_knife"),
            InlineKeyboardButton(text="🧤 Glove", callback_data=f"{prefix}_glove"),
        ],
        [InlineKeyboardButton(text="🌀 Both", callback_data=f"{prefix}_both")]
    ])

# --- Обработчик команды /start ---
@dp.message(CommandStart())
async def start_handler(message: Message):
    await message.answer("Выберите действие:", reply_markup=main_menu())

# --- Обработка нажатия на главное меню ---
@dp.callback_query(F.data.in_({"menu_parse", "menu_top10"}))
async def main_menu_handler(call: CallbackQuery, state: FSMContext):
    await call.answer()

    if call.data == "menu_parse":
        await call.message.edit_text(
            "Выберите тип предмета для парсинга:",
            reply_markup=type_selector("parse"),
            parse_mode=ParseMode.HTML
        )

    elif call.data == "menu_top10":
        await call.message.edit_text(
            "Выберите тип предмета для ТОП-10:",
            reply_markup=type_selector("top"),
            parse_mode=ParseMode.HTML
        )

    elif call.data == "menu_save_items":
        await call.message.edit_text(
            "✏️ Введите названия предметов для отслеживания (по одному на строке):",
            parse_mode=ParseMode.HTML
        )
        await state.set_state(WatchItemStates.waiting_for_item_text)


# Кнопка "Сохранить предметы"
@router.message(Command("watch_items"))
async def watch_items_command(message: types.Message, state: FSMContext):
    await message.answer("Введите часть названия предмета, который хотите отслеживать:")
    await state.set_state(WatchItemStates.waiting_for_item_text)

# Обработка текста от пользователя
@router.message(WatchItemStates.waiting_for_item_text)
async def handle_watch_item_input(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    query_text = message.text.strip()

    # Вызов функции сохранения предметов
    saved_items = await save_user_watch_items(user_id=user_id, search_text=query_text, conn=DB_CONFIG)

    if saved_items:
        await message.answer(
            f"Добавлены предметы в отслеживаемые:\n\n" + "\n".join(saved_items)
        )
    else:
        await message.answer("Не найдено предметов по вашему запросу.")
    await state.clear()


# --- Обработка выбора типа для парсинга ---
@dp.callback_query(F.data.startswith("parse_"))
async def parse_callback(call: CallbackQuery):
    await call.answer()
    item_type = call.data.removeprefix("parse_")
    msg = await call.message.edit_text(f"⏳ Запущен парсинг для: <b>{item_type}</b>...", parse_mode=ParseMode.HTML)
    result = parse_items(item_type)
    await msg.edit_text(f"✅ Результат парсинга для <b>{item_type}</b>:\n{result}", parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "adjust_prices")
async def handle_adjust_prices(call: CallbackQuery):
    result_text = adjust_prices()
    if result_text:
        
        msg = f"Результаты обновления цен:\n{result_text}"
    else:
        msg = f"Обновлять нечего"
    await call.message.answer(msg, parse_mode=ParseMode.HTML)
    await call.answer()

@dp.callback_query(F.data.startswith("top_"))
async def top_item_type_chosen(call: CallbackQuery):
    await call.answer()
    item_type = call.data.removeprefix("top_")
    user_id = call.from_user.id
    
    if item_type == "both":
        user_states[user_id] = {
            "step": "waiting_min_price",
            "item_type": item_type,
            "price_min": None,
            "price_max": None,
        }
        await call.message.edit_text(
            f"Выбран тип: <b>Both</b>\nВведите минимальную цену или отправьте '.' чтобы пропустить.",
            parse_mode=ParseMode.HTML
        )
        return
    # Инициализация выбранных подтипов пустым набором
    user_selected_subtypes[user_id] = set()

    # Сохраняем состояние
    user_states[user_id] = {
        "step": "selecting_subtypes",
        "item_type": item_type,
        "price_min": None,
        "price_max": None,
    }

    kb = build_subtype_keyboard(item_type, user_selected_subtypes[user_id])
    await call.message.edit_text(
        f"Выбран тип: <b>{item_type.capitalize()}</b>\n"
        f"Выберите один или несколько подтипов (нажмите, чтобы выбрать/снять выбор):",
        parse_mode=ParseMode.HTML,
        reply_markup=kb
    )

def build_subtype_keyboard(item_type: str, selected: set):
    if item_type == "knife":
        options = CS_KNIVES_LC
    elif item_type == "glove":
        options = CS_GLOVES_LC
    else:
        # Для both показываем и ножи и перчатки, можно объединить списки
        options = CS_KNIVES_LC + CS_GLOVES_LC

    keyboard = []
    for subtype in options:
        checked = "✅" if subtype in selected else "☑️"
        keyboard.append([
            InlineKeyboardButton(text=f"{checked} {subtype}", callback_data=f"subtype_toggle|{subtype}")
        ])

    # Добавим кнопку "Готово"
    keyboard.append([InlineKeyboardButton(text="✅ Дальше", callback_data="subtype_done")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)



# Обработка нажатия на подтипы
@dp.callback_query(F.data.startswith("subtype_toggle|"))
async def subtype_toggle_handler(call: CallbackQuery):
    await call.answer()
    user_id = call.from_user.id
    if user_id not in user_selected_subtypes:
        return  # Игнорируем если состояние потерялось

    subtype = call.data.removeprefix("subtype_toggle|")
    selected = user_selected_subtypes[user_id]

    if subtype in selected:
        selected.remove(subtype)
    else:
        selected.add(subtype)

    # Получаем item_type из user_states
    item_type = user_states.get(user_id, {}).get("item_type", "both")

    kb = build_subtype_keyboard(item_type, selected)
    await call.message.edit_reply_markup(reply_markup=kb)

# Обработка завершения выбора подтипов
@dp.callback_query(F.data == "subtype_done")
async def subtype_done_handler(call: CallbackQuery):
    await call.answer()
    user_id = call.from_user.id

    if user_id not in user_states or user_id not in user_selected_subtypes:
        return

    # Сохраняем выбранные подтипы в состояние
    user_states[user_id]["selected_subtypes"] = list(user_selected_subtypes[user_id])
    user_states[user_id]["step"] = "waiting_min_price"

    # Очистим временный список выбора
    user_selected_subtypes.pop(user_id, None)

    await call.message.edit_text(
        "Введите минимальную цену или отправьте '.' чтобы пропустить.",
        parse_mode=ParseMode.HTML
    )

# Обработка ввода цен остается без изменений, только теперь учитываем подтипы в запросе
@dp.message()
async def price_input_handler(message: Message):
    user_id = message.from_user.id
    if user_id not in user_states:
        return

    state = user_states[user_id]
    step = state.get("step")
    text = message.text.strip()

    if step == "waiting_min_price":
        if text == ".":
            state["price_min"] = None
        else:
            try:
                state["price_min"] = float(text.replace(",", "."))
            except ValueError:
                await message.reply("Пожалуйста, введите число или '.' для пропуска минимальной цены.")
                return
        state["step"] = "waiting_max_price"
        await message.answer("Введите максимальную цену или отправьте '.' чтобы пропустить.")

    elif step == "waiting_max_price":
        if text == ".":
            state["price_max"] = None
        else:
            try:
                state["price_max"] = float(text.replace(",", "."))
            except ValueError:
                await message.reply("Пожалуйста, введите число или '.' для пропуска максимальной цены.")
                return

        # Делаем запрос с учетом выбранных подтипов (если есть)
        selected_subtypes = state.get("selected_subtypes", None)
        if selected_subtypes:
            # Для фильтра в SQL используем список или конкатенацию с OR по подтипам
            # Здесь передадим список, а в SQL сделаем фильтр через ILIKE ANY(array[...])
            # Поэтому надо доработать get_top_10_rating_items, чтобы принимала список подтипов
            rows = get_top_10_rating_items(
                conn_params=DB_CONFIG,
                price_min=state["price_min"],
                price_max=state["price_max"],
                item_type=state["item_type"],
                subtype_list=selected_subtypes
            )
        else:
            rows = get_top_10_rating_items(
                conn_params=DB_CONFIG,
                price_min=state["price_min"],
                price_max=state["price_max"],
                item_type=state["item_type"],
                subtype_list=None
            )

        if not rows:
            await message.answer("⚠️ Нет данных для выбранных параметров.")
        else:
            response = f"<b>🔥 ТОП 10 предметов — {state['item_type'].capitalize()}:</b>\n\n"
            for i, row in enumerate(rows, start=1):
                ros = row.get('ros')
                ros_str = f"{ros} %" if ros is not None else "—"
                response += (
                    f"<b>{i}</b>. <code>{row['Предмет']}</code>\n"
                    f"💰 Маркет: {row['Маркет Цена']} $\n"
                    f"💲 Текущая цена: {row['Текущая Маркет Цена']} $\n"
                    f"🦊 ЛисСкинс: {row['Лисскинс Цена']} $\n"
                    f"🎯 ROS: {ros_str}\n"
                    f"📊 Чистая прибыль: {row['Чистая прибыль']} $\n"
                    f"📈 Текущая прибыль: {row['Текущая прибыль']} $\n"
                    f"🛒 Продаж за последние 7д: {row['Продаж за последние 7 дней']}\n"
                    f"🔗 <a href=\"{row['market_url']}\">Маркет</a> | <a href=\"{row['lisskins_url']}\">ЛисСкинс</a>\n\n"
                )
            await message.answer(response, parse_mode=ParseMode.HTML)

        user_states.pop(user_id, None)

# --- Запуск бота ---
async def main():
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    scheduler.add_job(schedule_parsing, "interval", minutes=5)
    scheduler.start()

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())