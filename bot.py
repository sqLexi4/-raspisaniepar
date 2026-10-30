"""Telegram-бот расписания. Запуск: python bot.py"""
import asyncio
import logging
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / "settings.txt")  # токен лежит в settings.txt

import parser_site  # noqa: E402
from timetable import (AUTO_FILES, DEFAULT_FORM, FORMS, courses_of, esc, format_day,  # noqa: E402
                       get_user, groups_of, load_schedule, save_auto, set_user,  # noqa: E402
                       users_count)  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("bot")

TZ = ZoneInfo("Europe/Minsk")  # часовой пояс; поменяй, если нужно
ADMIN_ID = int(os.getenv("ADMIN_ID") or 0)
FEEDBACK_USERNAME = (os.getenv("FEEDBACK_USERNAME") or "").strip().lstrip("@")   # из settings.txt
DONATE_URL = (os.getenv("DONATE_URL") or "").strip()                              # из settings.txt
router = Router()


def today() -> date:
    return datetime.now(TZ).date()


def day_keyboard(d: date, change_text: str = "🔄 Сменить группу"):
    """Листание дней."""
    kb = InlineKeyboardBuilder()
    kb.button(text="◀ Назад", callback_data=f"day:{(d - timedelta(days=1)).isoformat()}")
    kb.button(text="Сегодня", callback_data=f"day:{today().isoformat()}")
    kb.button(text="Вперёд ▶", callback_data=f"day:{(d + timedelta(days=1)).isoformat()}")
    kb.button(text=change_text, callback_data="change")
    kb.button(text="🏠 В меню", callback_data="menu")
    kb.adjust(3, 1, 1)
    return kb.as_markup()


def main_markup():
    kb = InlineKeyboardBuilder()
    kb.button(text="📅 Расписание", callback_data="change")
    kb.button(text="ℹ️ О боте", callback_data="about")
    kb.adjust(1)
    return kb.as_markup()


MAIN_TEXT = "👋 <b>Привет!</b> Я показываю расписание пар.\n\nЧто нужно?"


def about_text(ping_ms: int | None = None) -> str:
    lines = ["ℹ️ <b>О боте</b>", "", "Показываю расписание занятий для студенческих групп.", "",
             f"👥 Пользуются ботом: <b>{users_count()}</b>"]
    lines.append(f"🏓 Пинг: <b>{ping_ms} мс</b>" if ping_ms is not None else "🏓 Пинг: <b>недоступен</b>")
    if FEEDBACK_USERNAME:
        lines.append(f"✉️ Обратная связь: @{esc(FEEDBACK_USERNAME)}")
    if DONATE_URL:
        lines.append("❤️ Поддержка бота — кнопка ниже")
    return "\n".join(lines)


def about_markup():
    kb = InlineKeyboardBuilder()
    if FEEDBACK_USERNAME:
        kb.button(text="✉️ Обратная связь", url=f"https://t.me/{FEEDBACK_USERNAME}")
    if DONATE_URL:
        kb.button(text="❤️ Поддержать бота", url=DONATE_URL)
    kb.button(text="⬅ Назад", callback_data="menu")
    kb.adjust(1)
    return kb.as_markup()


def forms_markup():
    kb = InlineKeyboardBuilder()
    for key, title in FORMS.items():
        kb.button(text=f"🎓 {title} обучение", callback_data=f"frm:{key}")
    kb.adjust(1)
    kb.row(InlineKeyboardButton(text="⬅ Назад", callback_data="menu"))
    return kb.as_markup()


def faculties_markup(schedule: dict):
    kb = InlineKeyboardBuilder()
    for i, name in enumerate(sorted(schedule)):
        kb.button(text=name, callback_data=f"fac:{i}")  # номер вместо названия: лимит Telegram 64 байта
    kb.adjust(1)
    kb.row(InlineKeyboardButton(text="⬅ К форме обучения", callback_data="change"))
    return kb.as_markup()


def pending_form(uid: int) -> str:
    """Форма обучения, которую человек сейчас выбирает (day / ext)."""
    f = get_user(uid).get("pending_form", DEFAULT_FORM)
    return f if f in FORMS else DEFAULT_FORM


def chosen_form(uid: int) -> str:
    """Форма обучения уже выбранной группы. У тех, кто выбирал группу до появления заочного, её нет — это дневное."""
    f = get_user(uid).get("form", DEFAULT_FORM)
    return f if f in FORMS else DEFAULT_FORM


async def safe_edit(msg: Message, text: str, markup=None):
    try:
        await msg.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):  # «ничего не изменилось» — не ошибка
            raise


@router.message(Command("change"))
async def cmd_change(m: Message):
    if not any(load_schedule(f) for f in FORMS):
        await m.answer("😔 Расписание пока не загружено. Загляни позже.")
        return
    set_user(m.from_user.id)
    await m.answer("📅 <b>Расписание</b>\n\nВыбери <b>форму обучения</b>:", reply_markup=forms_markup())


@router.message(CommandStart())
async def cmd_start(m: Message):
    if not any(load_schedule(f) for f in FORMS):
        await m.answer("😔 Расписание пока не загружено. Загляни позже.")
        return
    set_user(m.from_user.id)   # запоминаем человека — по этому считаем, сколько людей пользуется ботом
    await m.answer(MAIN_TEXT, reply_markup=main_markup())


@router.message(Command("help"))
async def cmd_help(m: Message):
    await m.answer("ℹ️ /start — главное меню (расписание, о боте)\n"
                   "/change — сразу выбрать группу\n"
                   "Кнопки ◀ ▶ листают дни, «Сегодня» возвращает к текущей дате.")


@router.callback_query(F.data == "menu")
async def cb_menu(cb: CallbackQuery):
    await safe_edit(cb.message, MAIN_TEXT, main_markup())
    await cb.answer()


@router.callback_query(F.data == "about")
async def cb_about(cb: CallbackQuery):
    try:
        t0 = time.perf_counter()
        await cb.bot.get_me()   # реальный запрос к Telegram API — меряем время ответа
        ping = round((time.perf_counter() - t0) * 1000)
    except Exception:
        ping = None
    await safe_edit(cb.message, about_text(ping), about_markup())
    await cb.answer()


@router.callback_query(F.data == "change")
async def cb_change(cb: CallbackQuery):
    await safe_edit(cb.message, "📅 <b>Расписание</b>\n\nВыбери <b>форму обучения</b>:", forms_markup())
    await cb.answer()



async def show_faculties(msg: Message, form: str):
    await safe_edit(msg, f"🎓 <b>{FORMS[form]} обучение</b>\n\nВыбери свой <b>факультет</b>:",
                    faculties_markup(load_schedule(form)))


@router.callback_query(F.data.startswith("frm:"))
async def cb_form(cb: CallbackQuery):
    form = cb.data.split(":")[1]
    if form not in FORMS:
        await cb.answer("Кнопка устарела. Нажми /start", show_alert=True)
        return
    if not load_schedule(form):
        await cb.answer(f"Расписание ({FORMS[form].lower()} обучение) пока не загружено. Загляни позже.", show_alert=True)
        return
    set_user(cb.from_user.id, pending_form=form)
    await show_faculties(cb.message, form)
    await cb.answer()


@router.callback_query(F.data == "back_fac")
async def cb_back_faculties(cb: CallbackQuery):
    await show_faculties(cb.message, pending_form(cb.from_user.id))
    await cb.answer()


def course_label(n: int) -> str:
    return f"{n} курс" if n else "Другие группы"


async def show_courses(msg: Message, form: str, faculty: str):
    kb = InlineKeyboardBuilder()
    for n in courses_of(load_schedule(form), faculty):
        kb.button(text=course_label(n), callback_data=f"crs:{n}")   # в кнопке — сам номер курса
    kb.adjust(2)
    kb.row(InlineKeyboardButton(text="⬅ К факультетам", callback_data="back_fac"))
    await safe_edit(msg, f"🎓 {FORMS[form]} обучение\n🏛 <b>{esc(faculty)}</b>\n\nВыбери <b>курс</b>:", kb.as_markup())


@router.callback_query(F.data.startswith("fac:"))
async def cb_faculty(cb: CallbackQuery):
    form = pending_form(cb.from_user.id)
    schedule = load_schedule(form)
    try:
        name = sorted(schedule)[int(cb.data.split(":")[1])]
    except (ValueError, IndexError):
        await cb.answer("Кнопка устарела. Нажми /start", show_alert=True)
        return
    set_user(cb.from_user.id, pending_faculty=name)
    await show_courses(cb.message, form, name)
    await cb.answer()


@router.callback_query(F.data == "back_crs")
async def cb_back_courses(cb: CallbackQuery):
    form = pending_form(cb.from_user.id)
    faculty = get_user(cb.from_user.id).get("pending_faculty")
    if faculty not in load_schedule(form):
        await cb.answer("Кнопка устарела. Нажми /start", show_alert=True)
        return
    await show_courses(cb.message, form, faculty)
    await cb.answer()


@router.callback_query(F.data.startswith("crs:"))
async def cb_course(cb: CallbackQuery):
    form = pending_form(cb.from_user.id)
    faculty = get_user(cb.from_user.id).get("pending_faculty")
    try:
        course = int(cb.data.split(":")[1])
        groups = groups_of(load_schedule(form), faculty, course)
    except (ValueError, TypeError):
        groups = []
    if not groups:
        await cb.answer("Кнопка устарела. Нажми /start", show_alert=True)
        return
    set_user(cb.from_user.id, pending_course=course)
    kb = InlineKeyboardBuilder()
    for i, g in enumerate(groups):
        kb.button(text=g, callback_data=f"grp:{i}")
    kb.adjust(2)
    kb.row(InlineKeyboardButton(text="⬅ К курсам", callback_data="back_crs"))
    await safe_edit(cb.message,
                    f"🎓 {FORMS[form]} обучение\n🏛 <b>{esc(faculty)}</b>\n📖 {course_label(course)}\n\nТеперь выбери <b>группу</b>:",
                    kb.as_markup())
    await cb.answer()


@router.callback_query(F.data.startswith("grp:"))
async def cb_group(cb: CallbackQuery):
    user = get_user(cb.from_user.id)
    form = pending_form(cb.from_user.id)
    faculty, course = user.get("pending_faculty"), user.get("pending_course")
    try:
        group = groups_of(load_schedule(form), faculty, course)[int(cb.data.split(":")[1])]
    except (ValueError, IndexError, TypeError):
        await cb.answer("Кнопка устарела. Нажми /start", show_alert=True)
        return
    set_user(cb.from_user.id, form=form, faculty=faculty, group=group)
    d = today()
    await safe_edit(cb.message, format_day(faculty, group, d, form), day_keyboard(d))
    await cb.answer()


@router.callback_query(F.data.startswith("day:"))
async def cb_day(cb: CallbackQuery):
    user = get_user(cb.from_user.id)
    form = chosen_form(cb.from_user.id)
    faculty, group = user.get("faculty"), user.get("group")
    if group not in load_schedule(form).get(faculty, {}):
        await cb.answer("Сначала выбери группу: /start", show_alert=True)
        return
    try:
        d = date.fromisoformat(cb.data.split(":", 1)[1])
    except ValueError:
        await cb.answer("Кнопка устарела. Нажми /start", show_alert=True)
        return
    await safe_edit(cb.message, format_day(faculty, group, d, form), day_keyboard(d))
    await cb.answer()


async def parser_job(forms=None) -> dict[str, bool]:
    """Загрузка расписания с сайта (по умолчанию — обе формы обучения). Любая ошибка попадает в лог,
    бот продолжает работать на старых данных; сбой одной формы не мешает другой."""
    ok = {}
    for form in forms or FORMS:
        try:
            data = await asyncio.to_thread(parser_site.fetch_and_parse, form)  # requests «медленный» — уводим в поток
            save_auto(data, form)
            logger.info("Расписание с сайта обновлено: %s обучение", FORMS[form].lower())
            ok[form] = True
        except NotImplementedError as e:
            logger.warning("%s", e)
            ok[form] = False
        except Exception:
            logger.exception("Не удалось обновить расписание (%s обучение) — остаются старые данные", FORMS[form].lower())
            ok[form] = False
    return ok


@router.message(Command("update"))
async def cmd_update(m: Message):
    if not ADMIN_ID or m.from_user.id != ADMIN_ID:
        return
    await m.answer("⏳ Загружаю расписание с сайта…")
    result = await parser_job()
    await m.answer("\n".join(f"{'✅' if ok else '⚠️'} {FORMS[f]} обучение: {'готово' if ok else 'не получилось, смотри логи'}"
                             for f, ok in result.items()))


async def main():
    token = os.getenv("BOT_TOKEN", "")
    if ":" not in token:
        raise SystemExit("Токен не найден. Открой settings.txt и вставь токен после BOT_TOKEN=")
    bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    scheduler = AsyncIOScheduler(timezone=TZ)
    scheduler.add_job(parser_job, "cron", day_of_week="fri", hour=18, minute=0)  # каждую пятницу в 18:00
    scheduler.start()
    missing = [f for f, path in AUTO_FILES.items() if not path.exists()]
    if missing:
        asyncio.create_task(parser_job(missing))   # первая загрузка сразу, не дожидаясь пятницы
    logger.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
