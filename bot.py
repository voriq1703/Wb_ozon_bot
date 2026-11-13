import asyncio
import json
import logging
import shutil
from pathlib import Path
import aiohttp

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
# Для продакшена лучше Redis, но пока оставляем Memory + сохранение черновиков в БД при отмене

from config import BOT_TOKEN, ADMIN_IDS, GROK_WEBHOOK_URL
from database import (
    init_db, get_or_create_user, add_balance, get_balance, 
    deduct_balance, save_request, get_recent_requests, get_user_info,
    get_user_requests, get_request_by_id, set_request_status, set_balance, get_users_with_balance,
    save_draft, load_draft, delete_draft, cleanup_old_drafts, save_support_ticket, get_stuck_requests
)

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

class RequestForm(StatesGroup):
    product = State()
    advantages = State()
    characteristics = State()
    photos = State()
    audience = State()
    extra = State()
    confirm = State()

class SupportForm(StatesGroup):
    request_number = State()
    description = State()

def main_menu(user_id: int = None):
    buttons = [
        [InlineKeyboardButton(text="Создать заявку", callback_data="create_request")],
        [InlineKeyboardButton(text="Мои заявки", callback_data="my_requests")],
        [InlineKeyboardButton(text="Мой баланс", callback_data="balance")],
        [InlineKeyboardButton(text="Купить пакет", callback_data="buy")],
    ]
    if user_id and user_id in ADMIN_IDS:
        buttons.append([InlineKeyboardButton(text="Админ-команды", callback_data="admin_help")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def skip_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Пропустить", callback_data="skip")],
        [InlineKeyboardButton(text="Далее", callback_data="next_anyway")],
        [InlineKeyboardButton(text="Назад", callback_data="back")],
        [InlineKeyboardButton(text="Отменить заявку", callback_data="cancel")]
    ])

def photo_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Без фото", callback_data="no_photo")],
        [InlineKeyboardButton(text="Назад", callback_data="back")],
        [InlineKeyboardButton(text="Отменить заявку", callback_data="cancel")]
    ])

def extra_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Нет", callback_data="no_extra")],
        [InlineKeyboardButton(text="Назад", callback_data="back")],
        [InlineKeyboardButton(text="Отменить заявку", callback_data="cancel")]
    ])

def confirm_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Отправить заявку", callback_data="send_request")],
        [InlineKeyboardButton(text="Назад", callback_data="back")],
        [InlineKeyboardButton(text="Отмена", callback_data="cancel")]
    ])

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await get_or_create_user(message.from_user.id, message.from_user.username)
    await message.answer(
        "Здравствуйте. Я помогу создать карточку товара для маркетплейса.\n\nВыберите действие:",
        reply_markup=main_menu()
    )

STATUS_MAP = {
    "new": "Новая",
    "in_progress": "В работе",
    "done": "Готово",
    "completed": "Выполнена",
    "cancelled": "Отменена"
}

@dp.callback_query(F.data == "my_requests")
async def my_requests(callback: CallbackQuery):
    rows = await get_user_requests(callback.from_user.id)
    if not rows:
        await callback.message.answer("У вас пока нет заявок.", reply_markup=main_menu(callback.from_user.id))
        await callback.answer()
        return
    text = "Ваши заявки:\n\n"
    for r in rows[:10]:
        status = STATUS_MAP.get(r[1], r[1])
        text += f"№{r[0]} — {status}\n"
    await callback.message.answer(text, reply_markup=main_menu(callback.from_user.id))
    await callback.answer()
async def show_balance(callback: CallbackQuery):
    bal = await get_balance(callback.from_user.id)
    await callback.message.answer(f"Ваш текущий баланс: {bal} заявок.", reply_markup=main_menu(callback.from_user.id))
    await callback.answer()

@dp.callback_query(F.data == "support")
async def start_support(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SupportForm.request_number)
    await callback.message.answer(
        "Напишите номер заявки, по которой возникла проблема (или «нет», если заявки ещё нет)."
    )
    await callback.answer()

@dp.message(SupportForm.request_number)
async def support_request_number(message: Message, state: FSMContext):
    await state.update_data(request_number=message.text)
    await state.set_state(SupportForm.description)
    await message.answer("Опишите проблему подробнее.")

@dp.message(SupportForm.description)
async def support_description(message: Message, state: FSMContext):
    data = await state.get_data()
    ticket_id = await save_support_ticket(
        message.from_user.id,
        data.get("request_number", "нет"),
        message.text
    )
    # Папка для обращения
    ticket_dir = Path(f"Обращения/{ticket_id}")
    ticket_dir.mkdir(parents=True, exist_ok=True)
    (ticket_dir / "описание.txt").write_text(
        f"Пользователь: {message.from_user.id} (@{message.from_user.username})\n"
        f"Заявка: {data.get('request_number')}\n\n"
        f"{message.text}",
        encoding="utf-8"
    )

    await message.answer(
        f"Обращение №{ticket_id} создано. Мы свяжемся с вами в ближайшее время.",
        reply_markup=main_menu(message.from_user.id)
    )

    # Уведомление админу
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"Новое обращение №{ticket_id}\n"
                f"От: {message.from_user.id} (@{message.from_user.username})\n"
                f"Заявка: {data.get('request_number')}\n\n"
                f"{message.text}"
            )
        except Exception:
            pass

    await state.clear()
async def buy_package(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1 заявка", callback_data="pkg_1")],
        [InlineKeyboardButton(text="20 заявок", callback_data="pkg_20")],
        [InlineKeyboardButton(text="50 заявок", callback_data="pkg_50")],
        [InlineKeyboardButton(text="100 заявок", callback_data="pkg_100")],
        [InlineKeyboardButton(text="В меню", callback_data="menu")]
    ])
    await callback.message.answer("Выберите пакет:", reply_markup=kb)
    await callback.answer()

@dp.callback_query(F.data.startswith("pkg_"))
async def package_selected(callback: CallbackQuery):
    amount = int(callback.data.split("_")[1])
    await callback.message.answer(
        f"Вы выбрали пакет на {amount} заявок.\n\n"
        "Для оплаты переведите сумму на реквизиты (заглушка):\n"
        "СБП: +7 XXX XXX-XX-XX\n\n"
        "После оплаты напишите администратору. Заявки будут начислены после подтверждения.",
        reply_markup=main_menu()
    )
    await callback.answer()

@dp.callback_query(F.data == "create_request")
async def start_request(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    # Сначала пробуем загрузить черновик из базы
    draft_data, draft_state = await load_draft(user_id)
    if draft_data and draft_state:
        await state.update_data(draft=draft_data, draft_state=draft_state)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Продолжить", callback_data="continue_draft")],
            [InlineKeyboardButton(text="Начать заново", callback_data="new_request")],
            [InlineKeyboardButton(text="В меню", callback_data="menu")]
        ])
        await callback.message.answer(
            "У вас есть незавершённая заявка. Продолжить с того места или начать заново?",
            reply_markup=kb
        )
        await callback.answer()
        return

    bal = await get_balance(user_id)
    is_admin = user_id in ADMIN_IDS
    if bal < 1 and not is_admin:
        await callback.message.answer(
            "На вашем балансе недостаточно заявок. Сначала приобретите пакет.",
            reply_markup=main_menu(user_id)
        )
        await callback.answer()
        return
    await state.set_state(RequestForm.product)
    await state.update_data(skipped=[], is_admin_request=is_admin)
    await callback.message.answer(
        "Шаг 1 из 6\n\nНапишите, что за товар. Укажите название и коротко опишите, что это."
    )
    await callback.answer()

@dp.message(RequestForm.product)
async def process_product(message: Message, state: FSMContext):
    await state.update_data(product=message.text)
    await state.set_state(RequestForm.advantages)
    await message.answer(
        "Перечислите 3–5 главных преимуществ, которые нужно показать на изображениях. Можно списком.",
        reply_markup=skip_kb()
    )

@dp.message(RequestForm.advantages)
async def process_advantages(message: Message, state: FSMContext):
    if len(message.text.strip()) < 10:
        await message.answer(
            "Ответ получился слишком коротким. Перечислите, пожалуйста, 3–5 главных преимуществ.",
            reply_markup=skip_kb()
        )
        return
    await state.update_data(advantages=message.text)
    await state.set_state(RequestForm.characteristics)
    await message.answer(
        "Шаг 3 из 6\n\n"
        "Укажите основные характеристики товара или пришлите ссылку на карточку / описание.",
        reply_markup=skip_kb()
    )

@dp.callback_query(RequestForm.advantages, F.data == "skip")
async def skip_advantages(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    skipped = data.get("skipped", [])
    skipped.append("преимущества")
    await state.update_data(advantages="не указано", skipped=skipped)
    await state.set_state(RequestForm.characteristics)
    await callback.message.answer(
        "Укажите основные характеристики товара или пришлите ссылку на карточку / описание.",
        reply_markup=skip_kb()
    )
    await callback.answer()

@dp.message(RequestForm.characteristics)
async def process_characteristics(message: Message, state: FSMContext):
    if len(message.text.strip()) < 5:
        await message.answer(
            "Не вижу характеристик. Укажите основные или пришлите ссылку.",
            reply_markup=skip_kb()
        )
        return
    await state.update_data(characteristics=message.text)
    await state.set_state(RequestForm.photos)
    await message.answer(
        "Пришлите фотографии реального товара (желательно несколько ракурсов).\n\n"
        "Лучше прислать фотографии реального товара — так результат будет точнее и качественнее.",
        reply_markup=photo_kb()
    )

@dp.callback_query(RequestForm.characteristics, F.data == "skip")
async def skip_characteristics(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    skipped = data.get("skipped", [])
    skipped.append("характеристики")
    await state.update_data(characteristics="не указано", skipped=skipped)
    await state.set_state(RequestForm.photos)
    await callback.message.answer(
        "Пришлите фотографии реального товара (желательно несколько ракурсов).\n\n"
        "Лучше прислать фотографии реального товара — так результат будет точнее и качественнее.",
        reply_markup=photo_kb()
    )
    await callback.answer()

@dp.message(RequestForm.photos, F.photo)
async def process_photos(message: Message, state: FSMContext):
    data = await state.get_data()
    photos = data.get("photo_files", [])
    if len(photos) >= 10:
        await message.answer("Можно прикрепить не более 10 фотографий.")
        return

    user_id = message.from_user.id
    draft_dir = Path(f"temp_requests/{user_id}")
    draft_dir.mkdir(parents=True, exist_ok=True)

    for photo in message.photo:
        if photo.file_size and photo.file_size > 10 * 1024 * 1024:
            await message.answer("Одно из фото слишком большое (больше 10 МБ).")
            return
        file = await bot.get_file(photo.file_id)
        path = draft_dir / f"{photo.file_id}.jpg"
        await bot.download_file(file.file_path, path)
        path_str = str(path)
        if path_str not in photos:
            photos.append(path_str)

    await state.update_data(photos="фото получены", photo_files=photos)
    await state.set_state(RequestForm.audience)
    await message.answer("Фото получены.")
    await message.answer("Шаг 5 из 6\n\nДля кого предназначен этот товар?", reply_markup=skip_kb())

@dp.callback_query(RequestForm.photos, F.data == "no_photo")
async def no_photo(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    skipped = data.get("skipped", [])
    skipped.append("фото")
    await state.update_data(photos="без фото", skipped=skipped)
    await state.set_state(RequestForm.audience)
    await callback.message.answer(
        "Для кого предназначен этот товар? Кому он в первую очередь нужен или под какую задачу.",
        reply_markup=skip_kb()
    )
    await callback.answer()

@dp.message(RequestForm.audience)
async def process_audience(message: Message, state: FSMContext):
    if len(message.text.strip()) < 5:
        await message.answer(
            "Не совсем понятно, для кого предназначен товар. Уточните, пожалуйста.",
            reply_markup=skip_kb()
        )
        return
    await state.update_data(audience=message.text)
    await state.set_state(RequestForm.extra)
    await message.answer(
        "Шаг 6 из 6\n\n"
        "Хотите что-то ещё добавить? Напишите или нажмите «Нет».",
        reply_markup=extra_kb()
    )

@dp.callback_query(RequestForm.audience, F.data == "skip")
async def skip_audience(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    skipped = data.get("skipped", [])
    skipped.append("аудитория")
    await state.update_data(audience="не указано", skipped=skipped)
    await state.set_state(RequestForm.extra)
    await callback.message.answer(
        "Хотите что-то ещё добавить? Напишите или нажмите «Нет».",
        reply_markup=extra_kb()
    )
    await callback.answer()

@dp.callback_query(RequestForm.extra, F.data == "no_extra")
async def no_extra(callback: CallbackQuery, state: FSMContext):
    await state.update_data(extra="нет")
    await show_summary(callback.message, state)
    await callback.answer()

@dp.message(RequestForm.extra)
async def process_extra(message: Message, state: FSMContext):
    await state.update_data(extra=message.text)
    await show_summary(message, state)

async def show_summary(message: Message, state: FSMContext):
    data = await state.get_data()
    skipped = data.get("skipped", [])
    text = (
        "Проверьте данные заявки:\n\n"
        f"Товар: {data.get('product', '—')}\n"
        f"Преимущества: {data.get('advantages', '—')}\n"
        f"Характеристики: {data.get('characteristics', '—')}\n"
        f"Фото: {data.get('photos', '—')}\n"
        f"Для кого: {data.get('audience', '—')}\n"
        f"Дополнительно: {data.get('extra', '—')}\n"
    )
    if skipped:
        text += f"\nВнимание: не указаны следующие данные: {', '.join(skipped)}.\nЭто может повлиять на итоговый результат."
    text += "\n\nОтправить заявку?"
    await state.set_state(RequestForm.confirm)
    await message.answer(text, reply_markup=confirm_kb())

async def send_to_grok(payload: dict) -> tuple[bool, str]:
    """
    Отправка заявки в Grok Bot через промежуточный webhook.
    Ожидается, что по GROK_WEBHOOK_URL висит сервис, который:
    - принимает POST с JSON
    - запускает Менеджера карточек с этими данными
    - возвращает 200 при успехе
    """
    if not GROK_WEBHOOK_URL:
        return False, "GROK_WEBHOOK_URL не задан в .env"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                GROK_WEBHOOK_URL,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=45)
            ) as resp:
                if resp.status == 200:
                    return True, ""
                body = await resp.text()
                return False, f"HTTP {resp.status}: {body[:300]}"
    except asyncio.TimeoutError:
        return False, "Таймаут при обращении к Grok webhook"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"

@dp.callback_query(RequestForm.confirm, F.data == "send_request")
async def send_request(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if data.get("sending"):
        await callback.answer("Заявка уже отправляется...", show_alert=True)
        return
    await state.update_data(sending=True)

    user_id = callback.from_user.id
    is_admin_request = data.get("is_admin_request", False)

    if not is_admin_request:
        if not await deduct_balance(user_id, 1):
            await state.update_data(sending=False)
            await callback.message.answer("Недостаточно заявок на балансе.", reply_markup=main_menu(user_id))
            await callback.answer()
            return

    request_id = await save_request(user_id, data)
    payload = {
        "request_id": request_id,
        "user_id": user_id,
        "username": callback.from_user.username,
        "data": data
    }

    success, error = await send_to_grok(payload)

    bal = await get_balance(user_id)
    if success:
        await callback.message.answer(
            f"Заявка №{request_id} принята в работу.\nРезультат придёт в этот чат.\nТекущий баланс: {bal} заявок.",
            reply_markup=main_menu(user_id)
        )
    else:
        # Принимаем заявку в любом случае, но уведомляем админа
        await callback.message.answer(
            f"Заявка №{request_id} принята.\nРезультат придёт в этот чат.\nТекущий баланс: {bal} заявок.",
            reply_markup=main_menu(user_id)
        )
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"Заявка №{request_id} от {user_id} (@{callback.from_user.username}) принята, "
                    f"но webhook недоступен.\nПричина: {error}\n\nДанные сохранены в базе."
                )
            except Exception:
                pass
        await set_request_status(request_id, "webhook_failed")

    await state.clear()
    await callback.answer()

@dp.callback_query(F.data == "cancel")
async def cancel(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    current_state = await state.get_state()
    if current_state:
        await save_draft(callback.from_user.id, data, current_state)
    await state.clear()
    await callback.message.answer(
        "Заявка сохранена как черновик. Вы можете продолжить её позже.",
        reply_markup=main_menu(callback.from_user.id)
    )
    await callback.answer()

@dp.callback_query(F.data == "continue_draft")
async def continue_draft(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    draft_data, draft_state = await load_draft(user_id)
    if not draft_data or not draft_state:
        await callback.answer("Черновик не найден")
        return
    await state.set_data(draft_data)
    await state.set_state(draft_state)
    # Показываем вопрос
    if "product" in str(draft_state):
        await callback.message.answer("Шаг 1 из 6\n\nНапишите, что за товар.")
    elif "advantages" in str(draft_state):
        await callback.message.answer("Шаг 2 из 6\n\nПеречислите преимущества.", reply_markup=skip_kb())
    elif "characteristics" in str(draft_state):
        await callback.message.answer("Шаг 3 из 6\n\nУкажите характеристики.", reply_markup=skip_kb())
    elif "photos" in str(draft_state):
        await callback.message.answer("Шаг 4 из 6\n\nПришлите фото.", reply_markup=photo_kb())
    elif "audience" in str(draft_state):
        await callback.message.answer("Шаг 5 из 6\n\nДля кого товар?", reply_markup=skip_kb())
    elif "extra" in str(draft_state):
        await callback.message.answer("Шаг 6 из 6\n\nЧто-то ещё добавить?", reply_markup=extra_kb())
    else:
        await show_summary(callback.message, state)
    await callback.answer()

@dp.callback_query(F.data == "new_request")
async def new_request(callback: CallbackQuery, state: FSMContext):
    await delete_draft(callback.from_user.id)
    data = await state.get_data()
    for p in data.get("photo_files", []):
        try:
            Path(p).unlink(missing_ok=True)
        except Exception:
            pass
    await state.clear()
    await start_request(callback, state)

@dp.callback_query(F.data == "new_request")
async def new_request(callback: CallbackQuery, state: FSMContext):
    # Удаляем черновик и временные файлы
    data = await state.get_data()
    photo_files = data.get("draft", {}).get("photo_files", [])
    for p in photo_files:
        try:
            Path(p).unlink(missing_ok=True)
        except Exception:
            pass
    await state.clear()
    # Запускаем новую заявку
    await start_request(callback, state)

@dp.callback_query(F.data == "menu")
async def to_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("Главное меню:", reply_markup=main_menu())
    await callback.answer()

@dp.callback_query(F.data == "back")
async def go_back(callback: CallbackQuery, state: FSMContext):
    current = await state.get_state()
    if current == RequestForm.advantages.state:
        await state.set_state(RequestForm.product)
        await callback.message.answer("Напишите, что за товар. Укажите название и коротко опишите, что это.")
    elif current == RequestForm.characteristics.state:
        await state.set_state(RequestForm.advantages)
        await callback.message.answer("Перечислите 3–5 главных преимуществ.", reply_markup=skip_kb())
    elif current == RequestForm.photos.state:
        await state.set_state(RequestForm.characteristics)
        await callback.message.answer("Укажите основные характеристики или пришлите ссылку.", reply_markup=skip_kb())
    elif current == RequestForm.audience.state:
        await state.set_state(RequestForm.photos)
        await callback.message.answer("Пришлите фотографии реального товара.", reply_markup=photo_kb())
    elif current == RequestForm.extra.state:
        await state.set_state(RequestForm.audience)
        await callback.message.answer("Для кого предназначен этот товар?", reply_markup=skip_kb())
    elif current == RequestForm.confirm.state:
        await state.set_state(RequestForm.extra)
        await callback.message.answer("Хотите что-то ещё добавить?", reply_markup=extra_kb())
    await callback.answer()

@dp.callback_query(F.data == "next_anyway")
async def next_anyway(callback: CallbackQuery, state: FSMContext):
    current = await state.get_state()
    if current == RequestForm.advantages.state:
        await state.set_state(RequestForm.characteristics)
        await callback.message.answer("Укажите основные характеристики или пришлите ссылку.", reply_markup=skip_kb())
    elif current == RequestForm.characteristics.state:
        await state.set_state(RequestForm.photos)
        await callback.message.answer("Пришлите фотографии реального товара.", reply_markup=photo_kb())
    elif current == RequestForm.audience.state:
        await state.set_state(RequestForm.extra)
        await callback.message.answer("Хотите что-то ещё добавить?", reply_markup=extra_kb())
    await callback.answer()

@dp.message(F.text == "/requests")
async def admin_requests(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    rows = await get_recent_requests(15)
    if not rows:
        await message.answer("Заявок пока нет.")
        return
    text = "Последние заявки:\n\n"
    for r in rows:
        text += f"№{r[0]} | user: {r[1]} | {r[2]} | {r[3]}\n"
    await message.answer(text)

@dp.message(F.text.startswith("/user"))
async def admin_user(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        uid = int(message.text.split()[1])
        info = await get_user_info(uid)
        if not info:
            await message.answer("Пользователь не найден.")
            return
        await message.answer(f"User {uid}\nБаланс: {info[0]} заявок\nСоздан: {info[1]}")
    except Exception:
        await message.answer("Формат: /user user_id")

@dp.message(F.text.startswith("/urequests"))
async def admin_user_requests(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        uid = int(message.text.split()[1])
        rows = await get_user_requests(uid)
        if not rows:
            await message.answer("У пользователя нет заявок.")
            return
        text = f"Заявки пользователя {uid}:\n\n"
        for r in rows:
            text += f"№{r[0]} | {r[1]} | {r[2]}\n"
        await message.answer(text)
    except Exception:
        await message.answer("Формат: /urequests user_id")

@dp.message(F.text.startswith("/req"))
async def admin_request_detail(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        rid = int(message.text.split()[1])
        row = await get_request_by_id(rid)
        if not row:
            await message.answer("Заявка не найдена.")
            return
        await message.answer(
            f"Заявка №{row[0]}\n"
            f"User: {row[1]}\n"
            f"Статус: {row[3]}\n"
            f"Создана: {row[4]}\n\n"
            f"Данные:\n{row[2][:1000]}"
        )
    except Exception:
        await message.answer("Формат: /req номер_заявки")

@dp.message(F.text.startswith("/status"))
async def admin_set_status(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Формат: /status номер_заявки новый_статус")
        return
    try:
        rid = int(parts[1])
        status = parts[2].strip()
        await set_request_status(rid, status)
        await message.answer(f"Статус заявки №{rid} изменён на «{status}».")
    except ValueError:
        await message.answer("Номер заявки должен быть числом.")

@dp.message(F.text.startswith("/setbal"))
async def admin_set_balance(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("Формат: /setbal user_id количество")
        return
    try:
        uid = int(parts[1])
        amount = int(parts[2])
        await set_balance(uid, amount)
        await message.answer(f"Баланс пользователя {uid} установлен на {amount}.")
    except ValueError:
        await message.answer("user_id и количество должны быть числами.")

@dp.message(F.text == "/active")
async def admin_active_users(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    rows = await get_users_with_balance()
    if not rows:
        await message.answer("Нет пользователей с балансом.")
        return
    text = "Пользователи с балансом:\n\n"
    for r in rows:
        text += f"{r[0]} — {r[1]} заявок\n"
    await message.answer(text)
@dp.callback_query(F.data == "admin_help")
async def admin_help(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    text = (
        "Админ-команды:\n\n"
        "/add user_id количество — начислить заявки\n"
        "/setbal user_id количество — установить баланс\n"
        "/user user_id — информация о пользователе\n"
        "/urequests user_id — заявки пользователя\n"
        "/requests — последние заявки\n"
        "/req номер — детали заявки\n"
        "/status номер статус — сменить статус\n"
        "/active — пользователи с балансом\n"
        "/stats — статистика"
    )
    await callback.message.answer(text, reply_markup=main_menu(callback.from_user.id))
    await callback.answer()

async def admin_stats(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    # Простая статистика
    await message.answer("Статистика пока базовая. Позже добавим больше.")
async def admin_add(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("Формат: /add user_id количество")
        return
    try:
        uid = int(parts[1])
        amount = int(parts[2])
        if amount <= 0:
            await message.answer("Количество должно быть больше 0.")
            return
        await add_balance(uid, amount)
        await message.answer(f"Начислено {amount} заявок пользователю {uid}.")
        try:
            await bot.send_message(uid, f"На ваш баланс начислено {amount} заявок. Текущий баланс: {await get_balance(uid)} заявок.")
        except Exception:
            await message.answer("(Не удалось уведомить пользователя)")
    except ValueError:
        await message.answer("user_id и количество должны быть числами.")

@dp.message()
async def unknown_message(message: Message, state: FSMContext):
    current = await state.get_state()
    if current is None:
        await message.answer(
            "Я не совсем понял. Выберите действие в меню:",
            reply_markup=main_menu(message.from_user.id)
        )
    else:
        await message.answer(
            "Сейчас я жду ответ на предыдущий вопрос. "
            "Пожалуйста, ответьте на него или воспользуйтесь кнопками."
        )

@dp.callback_query()
async def unknown_callback(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Эта кнопка сейчас недоступна.", show_alert=True)
    if await state.get_state() is None:
        await callback.message.answer(
            "Выберите действие:",
            reply_markup=main_menu(callback.from_user.id)
        )

def cleanup_temp_for_request(request_id: int):
    """Удаляет временные файлы только конкретной заявки."""
    temp_dir = Path("temp_requests")
    if not temp_dir.exists():
        return
    request_temp = temp_dir / str(request_id)
    if request_temp.exists():
        shutil.rmtree(request_temp, ignore_errors=True)

async def periodic_cleanup():
    while True:
        await cleanup_old_drafts(7)
        await asyncio.sleep(7 * 24 * 60 * 60)  # раз в 7 дней

async def check_stuck_requests():
    while True:
        stuck = await get_stuck_requests(24)
        for req_id, user_id in stuck:
            await set_request_status(req_id, "delayed")
            try:
                await bot.send_message(
                    user_id,
                    f"Заявка №{req_id} обрабатывается дольше обычного. Мы уже проверяем статус."
                )
            except Exception:
                pass
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(admin_id, f"Заявка №{req_id} зависла (пользователь {user_id}).")
                except Exception:
                    pass
        await asyncio.sleep(6 * 60 * 60)  # проверка каждые 6 часов

async def main():
    await init_db()
    asyncio.create_task(periodic_cleanup())
    asyncio.create_task(check_stuck_requests())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
