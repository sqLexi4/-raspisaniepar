"""Данные расписания групп: чтение, склейка, красивое оформление.
Здесь нет кода Telegram — только работа с данными.

Откуда берутся пары:
  1) schedule.csv         — твоя таблица (редактируешь в Excel / Google Таблицах);
  2) data/schedule_auto.json — то, что загрузил парсер сайта (бот пишет сам).
Если на какой-то день для группы есть строки в schedule.csv — они ЗАМЕНЯЮТ данные парсера на этот день.
"""
import csv
import html
import io
import json
import logging
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

BASE = Path(__file__).parent
CSV_FILE = BASE / "schedule.csv"
DATA_DIR = BASE / "data"
DATA_DIR.mkdir(exist_ok=True)
AUTO_FILE = DATA_DIR / "schedule_auto.json"          # дневное (имя прежнее, чтобы не потерять данные)
EXT_FILE = DATA_DIR / "schedule_ext.json"            # заочное
USERS_FILE = DATA_DIR / "users.json"

# Формы обучения: внутренний ключ -> название на кнопке; ключ -> файл с данными парсера
FORMS = {"day": "Дневное", "ext": "Заочное"}
AUTO_FILES = {"day": AUTO_FILE, "ext": EXT_FILE}
DEFAULT_FORM = "day"
OTHER_FACULTY = "Другие группы"   # если по номеру группы факультет не определить

# Факультет определяется по ПЕРВОЙ цифре номера группы. Названия можно изменить здесь.
# Магистратура: группы, которые начинаются на 5.
FACULTY_NAMES = {"0": "ФОФК", "1": "СИиЕ", "2": "МВС", "4": "ИМСиТ", "5": "Магистратура"}

# Названия колонок в таблице (регистр не важен) -> внутренние имена
COLUMNS = {"факультет": "faculty", "группа": "group", "дата": "date", "пара": "pair",
           "время": "time", "предмет": "subject", "аудитория": "room", "преподаватель": "teacher",
           "форма": "form"}

WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря"]
NUMS = {1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣", 6: "6️⃣", 7: "7️⃣", 8: "8️⃣"}


# ---------- мелкие помощники ----------
def norm_form(text: str) -> str:
    """«заочное», «Заочная», «ext» -> "ext"; всё остальное (в т.ч. пусто) -> "day"."""
    t = (text or "").strip().lower()
    return "ext" if t.startswith(("заоч", "ext")) else "day"


def parse_date(text: str):
    """Понимает 2026-10-28, 28.10.2026, 28.10.26, 28/10/2026."""
    text = (text or "").strip().split(" ")[0]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("Не удалось прочитать %s", path)
        return {}


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)  # подмена файла целиком: при сбое старый файл не испортится


# ---------- чтение таблицы ----------
def read_csv_rows() -> list[dict]:
    if not CSV_FILE.exists():
        return []
    raw = CSV_FILE.read_bytes()
    for enc in ("utf-8-sig", "cp1251"):  # Excel иногда сохраняет в cp1251
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        logger.error("schedule.csv: не удалось определить кодировку")
        return []
    first = text.splitlines()[0] if text.strip() else ""
    delim = ";" if first.count(";") >= first.count(",") else ","  # Excel (рус.) любит «;»
    rows = []
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    for n, rec in enumerate(reader, start=2):
        row = {COLUMNS[k.strip().lower()]: (v or "").strip()
               for k, v in rec.items() if k and k.strip().lower() in COLUMNS}
        d = parse_date(row.get("date", ""))
        if not row.get("faculty") and row.get("group"):
            row["faculty"] = faculty_of_group(row["group"])
        if not (row.get("faculty") and row.get("group") and d):
            if any(row.values()):
                logger.warning("schedule.csv, строка %s пропущена: нужны группа и дата (и факультет, если номер группы нестандартный)", n)
            continue
        row["date"] = d.isoformat()
        row["form"] = norm_form(row.get("form", ""))
        try:
            row["pair"] = int(row.get("pair") or 0)
        except ValueError:
            row["pair"] = 0
        rows.append(row)
    return rows


# ---------- склейка ----------
def group_allowed(group: str, form: str = DEFAULT_FORM) -> bool:
    """Дневное: группы с номером на цифру, которой нет в FACULTY_NAMES, не показываем
    (номера с другой первой цифрой — не наши факультеты). Заочное: показываем все группы —
    номера там заранее неизвестны, неопознанные попадут в «Другие группы».
    Группы с нестандартным названием (не 4 цифры) не трогаем."""
    if form == "ext":
        return True
    m = re.match(r"\s*(\d{4})", group)
    return m is None or m.group(1)[0] in FACULTY_NAMES


def load_schedule(form: str = DEFAULT_FORM) -> dict:
    """Возвращает {факультет: {группа: {"YYYY-MM-DD": [пары]}}} для выбранной формы обучения."""
    result: dict = {}
    for faculty, groups in _read_json(AUTO_FILES[form]).items():
        for group, days in groups.items():
            if not group_allowed(group, form):
                continue
            default = OTHER_FACULTY if form == "ext" else faculty   # у заочного заголовки на сайте не надёжны
            result.setdefault(faculty_of_group(group, default), {}).setdefault(group, {}).update(days)
    days: dict = {}
    for r in read_csv_rows():
        if r["form"] != form or not group_allowed(r["group"], form):
            continue
        key = (faculty_of_group(r["group"], r["faculty"]), r["group"], r["date"])
        days.setdefault(key, []).append({k: r.get(k, "") for k in ("pair", "time", "subject", "room", "teacher")})
    for (f, g, d), pairs in days.items():
        result.setdefault(f, {}).setdefault(g, {})[d] = pairs
    return result


def save_auto(new: dict, form: str = DEFAULT_FORM) -> None:
    """Добавляет свежие данные парсера к уже сохранённым (по дням) и удаляет даты старше 45 дней
    (и «из будущего» дальше чем на год — это опечатки в PDF). Пустой результат не пишем —
    чтобы не затереть хорошие данные."""
    if not new:
        raise ValueError("Парсер вернул пустой результат — старые данные сохранены")
    path = AUTO_FILES[form]
    data = _read_json(path)
    for faculty, groups in new.items():
        for group, days in groups.items():
            for day, pairs in days.items():
                data.setdefault(faculty, {}).setdefault(group, {})[day] = pairs
    cutoff = (date.today() - timedelta(days=45)).isoformat()
    horizon = (date.today() + timedelta(days=366)).isoformat()
    for faculty in list(data):
        for group in list(data[faculty]):
            data[faculty][group] = {d: p for d, p in data[faculty][group].items() if cutoff <= d <= horizon}
            if not data[faculty][group]:
                del data[faculty][group]
        if not data[faculty]:
            del data[faculty]
    _write_json(path, data)


# ---------- факультет → курс → группа ----------
def faculty_of_group(group: str, default: str = "") -> str:
    """0xxx → ФОФК, 1xxx → СИиЕ, 2xxx → МВС, 4xxx → ИМСиТ, 5xxx → Магистратура.
    Если номер не распознан — default."""
    m = re.match(r"\s*(\d{4})", group)
    return FACULTY_NAMES.get(m.group(1)[0], default) if m else default



def course_of(group: str) -> int:
    """Курс берём из номера группы: вторая цифра (1107 → 1 курс, 1207 → 2, 2305 → 3).
    0 = номер не распознан (например, группа из ручной таблицы с другим названием)."""
    m = re.match(r"\s*(\d{4})", group)
    return int(m.group(1)[1]) if m else 0


def courses_of(schedule: dict, faculty: str) -> list[int]:
    return sorted({course_of(g) for g in schedule.get(faculty, {})})


def groups_of(schedule: dict, faculty: str, course: int) -> list[str]:
    return sorted(g for g in schedule.get(faculty, {}) if course_of(g) == course)


# ---------- пользователи ----------
def get_user(uid: int) -> dict:
    return _read_json(USERS_FILE).get(str(uid), {})


def users_count() -> int:
    return len(_read_json(USERS_FILE))


def set_user(uid: int, **fields) -> None:
    users = _read_json(USERS_FILE)
    users.setdefault(str(uid), {}).update(fields)
    _write_json(USERS_FILE, users)


# ---------- оформление ----------
def esc(s) -> str:
    """Telegram понимает HTML-теги, поэтому символы < > & нужно «обезвредить»."""
    return html.escape(str(s or ""))


def format_day(faculty: str, group: str, d: date, form: str = DEFAULT_FORM) -> str:
    pairs = load_schedule(form).get(faculty, {}).get(group, {}).get(d.isoformat(), [])
    head = (f"📅 <b>{WEEKDAYS[d.weekday()]}, {d.day} {MONTHS[d.month - 1]} {d.year}</b>\n"
            f"👥 {esc(group)} · {FORMS[form]}\n")
    if not pairs:
        return head + "\nПар нет 🎉"
    blocks = []
    for p in sorted(pairs, key=lambda x: int(x.get("pair") or 0)):
        n = int(p.get("pair") or 0)
        blocks.append(f"{NUMS.get(n, '▫️')} <b>{esc(p.get('time'))}</b>\n"
                      f"📚 {esc(p.get('subject'))}\n"
                      f"🚪 {esc(p.get('room')) or '—'}\n"
                      f"👨‍🏫 {esc(p.get('teacher')) or '—'}")
    return head + "\n" + "\n\n".join(blocks)
