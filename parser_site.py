"""Загрузка расписания с сайта БГУФК: дневное и заочное обучение (разные страницы сайта).

  дневное — https://www.sportedu.by/raspisanie-zanyatij/
  заочное — https://www.sportedu.by/raspisanie-zanyatij-zaochnoe/

Одна и та же функция разбора PDF (сетка «дни × время × группы») работает для обеих форм.
Если расписание группы растянуто на несколько страниц PDF (на продолжении нет шапки с номерами групп
или нет подписи дня) — страницы склеиваются, см. parse_pdf().
"""
import json
import logging
import os
import re
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlparse

import pdfplumber
import requests
from bs4 import BeautifulSoup

from timetable import faculty_of_group

logger = logging.getLogger(__name__)
URLS = {
    "day": "https://www.sportedu.by/raspisanie-zanyatij/",
    "ext": "https://www.sportedu.by/raspisanie-zanyatij-zaochnoe/",
}
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ScheduleBot/1.0)"}
PDF_DIR = Path(__file__).parent / "data" / "pdf"
GROUP_SPEC_FILE = Path(__file__).parent / "data" / "group_spec.json"   # {группа: специализация} — подпись под группой в PDF


def save_group_specs(specs: dict[str, str]) -> None:
    """Склеивает свежие специализации групп с сохранёнными и пишет в data/group_spec.json."""
    try:
        old = json.loads(GROUP_SPEC_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        old = {}
    old.update(specs)
    GROUP_SPEC_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = GROUP_SPEC_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(old, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, GROUP_SPEC_FILE)


HEAD_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "strong", "b", "p", "div", "span", "summary", "button", "li"}


def _clean(text: str) -> str:
    return " ".join(text.split())


def _faculty_of(a) -> str:
    """Ищем ближайший ВЫШЕ ссылки короткий заголовок со словом «факультет»."""
    def is_faculty(t):
        txt = _clean(t.get_text())
        return t.name in HEAD_TAGS and "факультет" in txt.lower() and len(txt) < 120
    h = a.find_previous(is_faculty)
    return _clean(h.get_text()) if h else "Без факультета"


def find_pdf_links(form: str = "day") -> list[tuple[str, str, str]]:
    """Возвращает список (факультет, подпись ссылки, URL) для всех PDF страницы выбранной формы обучения."""
    url_page = URLS[form]
    resp = requests.get(url_page, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    links, seen = [], set()
    for a in soup.find_all("a", href=True):
        full = urljoin(url_page, a["href"])
        if not urlparse(full).path.lower().endswith(".pdf") or full in seen:
            continue
        seen.add(full)
        label = _clean(a.get_text()) or Path(urlparse(full).path).stem
        links.append((_faculty_of(a), label, full))
    return links


def download_pdf(url: str, form: str = "day") -> Path:
    folder = PDF_DIR if form == "day" else PDF_DIR / form    # заочные PDF — в отдельной папке, чтобы имена не пересеклись
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / Path(urlparse(url).path).name
    resp = requests.get(url, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    path.write_bytes(resp.content)
    return path


# ======================= РАЗБОР PDF (сетка: дни × время × группы) =======================
TIME_RE = re.compile(r"^\s*(\d{1,2})[.:](\d{2})\s*[-–—]\s*(\d{1,2})[.:](\d{2})\s*$")
WEEKDAY_RE = re.compile(r"понедельник|вторник|среда|четверг|пятница|суббота|воскресенье", re.I)
DATE_RE = re.compile(r"(\d{1,2})\.(\d{2})(?:\.(\d{2,4}))?")
ROOM_RE = re.compile(r"\d|бассейн|манеж|УСК|тренаж|плоск|кафедр|л/а|л/атл|зал|стадион|сооруж", re.I)
TEACHER_WORD = re.compile(r"[А-ЯЁ][а-яё]+")
NOTE_TAIL_RE = re.compile(r"^(.*?)\s*(\((?:[^)]*п/гр[^)]*|лекция)\))$")
TEACHER_ROOM_RE = re.compile(r"^([А-ЯЁ][а-яё]+(?: [А-ЯЁ][а-яё]+)*)\s+(\S.*)$")
BELLS = {"08:30–09:55": 1, "10:10–11:35": 2, "12:05–13:30": 3, "13:45–15:10": 4,
         "15:25–16:50": 5, "17:05–18:30": 6, "18:50–20:15": 7}

# Словарь «правильных» названий предметов. В некоторых PDF буквы набраны «р а з р я д к о й» или с
# лишними пробелами («П Л А В АНИЕ») — по словарю такие названия восстанавливаются.
# Словарь пополняется сам: чистые названия из разбираемых PDF добавляются автоматически.
SEED_SUBJECTS = """АНАТОМИЯ|БЕЗОПАСНОСТЬ ЖИЗНЕДЕЯТЕЛЬНОСТИ ЧЕЛОВЕКА|БЕЛОРУССКИЙ ЯЗЫК|БЖЧ|БИОХИМИЯ|ВВЕД. В ТУРИЗМ|
ВВЕДЕНИЕ В ТУРИЗМ|ГЕОГРАФИЯ МЕЖД. ТУРИЗМА И ТУРИСТИЧ. СТРАНОВЕДЕНИЕ|ГЕОГРАФИЯ МЕЖДУНАРОДНОГО ТУРИЗМА|
ГИМНАСТИКА и МП|ИБГ|ИН. ЯЗЫК|ИНОСТРАННЫЙ ЯЗЫК|ИНФ. ТЕХН. В СПОРТЕ|ИНФОРМАЦИОННЫЕ ТЕХНОЛОГИИ|
ИСТОРИЯ БЕЛОРУССКОЙ ГОСУДАРСТВЕННОСТИ|ИСТОРИЯ ПУТЕШЕСТВИЙ И ТУРИЗМА|ИСТОРИЯ ФИЗИЧЕСКОЙ КУЛЬТУРЫ И СПОРТА|
ИСТОРИЯ ФКиС|ИТ в ФКиС|ИФКиС|КРАЕВЕДЕНИЕ|ЛЕГКАЯ АТЛЕТИКА и МП|ЛЕГКАЯ АТЛЕТИКА И МЕТОДИКА ПРЕПОДАВАНИЯ|
МОВОФК|НЕМЕЦКИЙ ЯЗЫК|ОСНОВЫ ВЫСШЕЙ МАТЕМАТИКИ|ОСНОВЫ МЕНЕДЖМЕНТА|ОСНОВЫ СПОРТ. И РЕКРЕАЦ. ТУРИЗМА|ОТиМИВС|
ПЛАВАНИЕ и МП|ПЛАВАНИЕ и МЕТОДИКА ПРЕПОДАВАНИЯ|ПОЛИТОЛОГИЯ|ППС|ПСМ|РЕКРЕАЛОГИЯ|СПП|СиПИ и МП|
ТиМФВдДДВ|Т-МОФРиЭ|ТиМОФК|ТиОАФК|ТиОЛФК|ФИЗИЧЕСКАЯ КУЛЬТУРА|ФРАНЦУЗСКИЙ ЯЗЫК|СМГ""".replace("\n", "").split("|")


def _key(text: str) -> str:
    return re.sub(r"[\s.,()\-]", "", text).upper().replace("Ё", "Е")


LEARNED: dict[str, str] = {_key(x): x for x in SEED_SUBJECTS}
BRACKETS_RE = re.compile(r"\s*(\([^)]*\))")


def _is_artifact(text: str) -> bool:
    """Есть ли «разрядка»: хотя бы две одиночные заглавные буквы подряд в названии."""
    return len(re.findall(r"(?<![\wЁё])[А-ЯЁ](?![\wЁё])", text)) >= 2


def _cell_text(page, cell) -> str:
    """Текст внутри ячейки (символ принадлежит ячейке, если его центр внутри)."""
    x0, top, x1, bottom = cell
    chars = [c for c in page.chars
             if x0 <= (c["x0"] + c["x1"]) / 2 < x1 and top <= (c["top"] + c["bottom"]) / 2 < bottom]
    return pdfplumber.utils.extract_text(chars).strip() if chars else ""


def _day_texts(page, cell) -> list[str]:
    """Варианты текста ячейки дня. Дата в PDF написана ВЕРТИКАЛЬНО, поэтому, кроме обычного текста,
    собираем текст по вертикальным строкам (символы одной строки имеют одинаковый x, читаются снизу вверх)."""
    x0, top, x1, bottom = cell
    chars = [c for c in page.chars if c["text"].strip()
             and x0 <= (c["x0"] + c["x1"]) / 2 < x1 and top <= (c["top"] + c["bottom"]) / 2 < bottom]
    lines: list[list] = []
    for c in sorted(chars, key=lambda c: c["x0"]):
        if lines and abs(lines[-1][0]["x0"] - c["x0"]) < 2.5:
            lines[-1].append(c)
        else:
            lines.append([c])
    vertical = " ".join("".join(ch["text"] for ch in sorted(l, key=lambda ch: -ch["top"])) for l in lines)
    return [vertical, _cell_text(page, cell)]


def _norm_time(m) -> str:
    h1, m1, h2, m2 = (int(x) for x in m.groups())
    return f"{h1:02d}:{m1:02d}–{h2:02d}:{m2:02d}"


def _split_commas(text: str) -> list[str]:
    """Делит по запятым, кроме запятых внутри скобок."""
    parts = re.split(r",(?![^()]*\))", text)
    out = []
    for part in (x.strip() for x in parts):
        if not part:
            continue
        if out and re.fullmatch(r"[а-яё]", part):      # «622а,б» — буква относится к предыдущему
            out[-1] += "," + part
        else:
            out.append(part)
    return out


def _clean_text(chunk: str) -> str:
    chunk = re.sub(r"\(([а-яё])(?: [а-яё])+\)", lambda m: m.group(0).replace(" ", ""), chunk)   # (л е к ц и я)
    chunk = re.sub(r"\b([А-ЯЁ]) ([а-яё]{2,})", r"\1\2", chunk)                                   # «К алендо»
    return chunk


def _fix_subject(subject: str) -> str:
    """Восстанавливает название по словарю (убирает разрядку), скобки сохраняет."""
    notes = "".join(" " + b for b in BRACKETS_RE.findall(subject))
    base = BRACKETS_RE.sub("", subject).strip()
    fixed = LEARNED.get(_key(base))
    if fixed:
        base = fixed
    return base + notes


def _learn(texts) -> None:
    """Пополняем словарь чистыми названиями из этого PDF (без одиночных букв-«разрядки»)."""
    for t in texts:
        for chunk in " ".join(t.split()).split(";"):
            toks = _split_commas(_clean_text(chunk.strip()))
            if not toks:
                continue
            base = BRACKETS_RE.sub("", toks[0]).strip()
            words = base.split()
            cut = next((i for i, w in enumerate(words) if i >= 1 and TEACHER_WORD.fullmatch(w)), None)
            if cut:
                base = " ".join(words[:cut])
            singles = [w for w in base.split() if len(w) == 1 and w not in "иВвИсС"]
            if len(base) >= 3 and re.fullmatch(r"[А-ЯЁа-яё .\-]+", base) and not singles and not _is_artifact(base):
                LEARNED.setdefault(_key(base), base)


# ---- ячейки вида «Предмет (Иванова О.В.) 231 манеж / English title (Ivanova O.V.) arena 231»
# (магистратура): преподаватель в скобках с инициалами, аудитория рядом, затем английский дубль.
_NAME = r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?\s+[А-ЯЁ]\.\s?[А-ЯЁ]\.?"
TEACHER_BR_RE = re.compile(rf"\(\s*{_NAME}(?:\s*,\s*{_NAME})*\s*\)")
ENGLISH_TAIL_RE = re.compile(r"\s*/\s*[A-Za-z][^А-Яа-яЁё]*")      # «/ English …» до следующей русской буквы
_ROOM_TOKEN = r"\d+[а-яa-z]?(?:\s*,\s*\d+[а-яa-z]?)*(?:\s+(?:манеж|УСК|бассейн|зал))?|кафедра(?:\s+[а-яё]+){1,3}"
ROOM_AFTER_RE = re.compile(rf"^\s*(?:{_ROOM_TOKEN})")
ROOM_BEFORE_RE = re.compile(rf"\s+({_ROOM_TOKEN})$")


def parse_bracket_cell(flat: str) -> list[dict]:
    """Ячейка, где преподаватель стоит в скобках. Возвращает [] если разобрать не удалось."""
    flat = " ".join(ENGLISH_TAIL_RE.sub(" ", flat).split())
    entries, pos = [], 0
    for m in TEACHER_BR_RE.finditer(flat):
        pre = flat[pos:m.start()].strip(" ,;")
        end = m.end()
        room_after = ROOM_AFTER_RE.match(flat[end:])
        rooms = []
        mb = ROOM_BEFORE_RE.search(pre)
        if mb:                                   # «… деят. 439 (Покровская С.Е.)»
            rooms.append(mb.group(1).strip())
            pre = pre[:mb.start()].strip(" ,;")
        if room_after:                           # «(Иванова О.В.) 231 манеж»
            rooms.append(room_after.group(0).strip())
            end += room_after.end()
        teacher = " ".join(m.group(0).strip("() ").split())
        if pre:
            entries.append({"subject": pre, "teacher": teacher, "room": ", ".join(rooms)})
        pos = end
    tail = flat[pos:].strip(" ,;")
    if tail and entries:
        entries[-1]["subject"] += " " + tail
    return entries


def parse_cell(text: str) -> list[dict]:
    """Текст ячейки -> список записей {subject, teacher, room}. Одна ячейка может содержать несколько
    записей через «;». Если не удалось разобрать — ничего не теряем: остаётся в subject."""
    entries = []
    flat = " ".join(text.split())
    if TEACHER_BR_RE.search(flat):
        bracket_entries = parse_bracket_cell(flat)
        if bracket_entries:
            return bracket_entries
    for chunk in (x.strip() for x in flat.split(";")):
        if not chunk:
            continue
        tokens = _split_commas(_clean_text(chunk))
        if not tokens:
            continue
        # 1) предмет (+ преподаватель, если запятой между ними нет: «ОСНОВЫ МЕНЕДЖМЕНТА Иванова»)
        words = tokens[0].split()
        cut = next((i for i, w in enumerate(words)
                    if i >= 1 and TEACHER_WORD.fullmatch(w) and not w.startswith("(")), None)
        if cut is not None:
            tokens = [" ".join(words[:cut]), " ".join(words[cut:])] + tokens[1:]
        subject, rest = _fix_subject(tokens[0]), tokens[1:]
        # 2) пометки (лекция), (1 п/гр) — к предмету; бывают и «приклеенные» к аудитории: «534 (1 п/гр)»
        notes, cleaned = [], []
        for t in rest:
            m = NOTE_TAIL_RE.match(t)
            if m and m.group(1):
                notes.append(m.group(2))
                if m.group(1).strip():
                    cleaned.append(m.group(1).strip())
            elif t.startswith("(") and t.endswith(")"):
                notes.append(t)
            else:
                cleaned.append(t)
        rest = cleaned
        if notes:
            subject += " " + " ".join(notes)
        # 3) «Подоляк УСК» / «Ночевная 533» — фамилия слиплась с аудиторией: разделяем
        fixed = []
        for t in rest:
            m = TEACHER_ROOM_RE.match(t)
            if m and ROOM_RE.search(m.group(2)):
                fixed += [m.group(1), m.group(2)]
            else:
                fixed.append(t)
        rest = fixed
        # 4) аудитория — «хвост» из подходящих токенов
        room = []
        while rest and ROOM_RE.search(rest[-1]):
            room.insert(0, rest.pop())
        entries.append({"subject": subject, "teacher": ", ".join(rest), "room": ", ".join(room)})
    return entries


def _day_date(candidates: list[str], year_start: int, year_end: int):
    """Из текста ячейки дня («ПОНЕДЕЛЬНИК 26.10», иногда перевёрнутого) -> date или None."""
    for text in candidates:
        flat = " ".join(text.split())
        for cand in (flat, flat[::-1]):
            if WEEKDAY_RE.search(cand):
                m = DATE_RE.search(cand)
                if m:
                    d, mo = int(m.group(1)), int(m.group(2))
                    inferred = year_start if mo >= 8 else year_end   # учебный год: сентябрь–декабрь / январь–июль
                    y = inferred
                    if m.group(3):
                        y = int(m.group(3))
                        y = y + 2000 if y < 100 else y
                        if not (year_start - 1 <= y <= year_end + 1):   # опечатка в PDF (например, 28.10.2029)
                            y = inferred
                    try:
                        return date(y, mo, d)
                    except ValueError:
                        pass
    return None


def _overlap(a0, a1, b0, b1) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def parse_pdf(path: Path, faculty: str, label: str) -> tuple[dict, dict]:
    """PDF с сеткой «дни × время × группы» -> (расписание, {группа: специализация}).
    Специализация — текст, написанный под номером группы в шапке таблицы."""
    result: dict = {}
    specs: dict = {}
    with pdfplumber.open(path) as pdf:
        full_text = " ".join((pg.extract_text() or "") for pg in pdf.pages)
        ym = re.search(r"(20\d\d)\s*/\s*(20\d\d)", full_text)
        y0, y1 = (int(ym.group(1)), int(ym.group(2))) if ym else (date.today().year, date.today().year)

        carry_groups: list = []   # шапка групп с предыдущей страницы: на «продолжениях» её нет
        carry_day = None          # последний день предыдущей страницы: день мог перейти на новую страницу
        for page in pdf.pages:
            tables = page.find_tables()
            if not tables:
                continue
            cells = sorted(set(max(tables, key=lambda t: len(t.cells)).cells), key=lambda c: (c[1], c[0]))
            texts = {c: _cell_text(page, c) for c in cells}

            # --- 1. строки времени
            slots = []   # (top, bottom, "08:30–09:55")
            time_x0 = time_x1 = None
            for c, t in texts.items():
                m = TIME_RE.match(t)
                if m:
                    slots.append((c[1], c[3], _norm_time(m)))
                    time_x0, time_x1 = c[0], c[2]
            if not slots:
                continue
            slots.sort()
            first_top = slots[0][0]
            order = sorted({s_[2] for s_ in slots})
            pair_no = {t: BELLS.get(t, 10 + i) for i, t in enumerate(order)}

            # --- 2. группы (шапка таблицы)
            header = [c for c in cells if c[3] <= first_top + 1 and c[0] >= time_x1 - 1]
            groups = []   # [x0, x1, название]
            for c in header:
                m = re.search(r"\b(\d{4})\b", texts[c])
                if m:
                    extra = " ".join(texts[c].replace(m.group(1), "", 1).split())
                    groups.append([c[0], c[2], m.group(1)])
                    if extra:
                        specs[m.group(1)] = extra   # под номером группы написана её специализация
            if groups:
                carry_groups = groups
            elif carry_groups:
                groups = carry_groups     # страница-продолжение: колонки те же, что на странице с шапкой
            else:
                continue

            # --- 3. дни (левая колонка)
            days = []     # (top, bottom, date)
            for c in cells:
                if c[2] <= time_x0 + 1 and c[1] >= first_top - 1:
                    d = _day_date(_day_texts(page, c), y0, y1)
                    if d:
                        days.append((c[1], c[3], d))

            days.sort()
            first_day_top = days[0][0] if days else float("inf")
            for g in groups:                       # каждый день из PDF фиксируем, даже если пар нет
                for _t0, _t1, d in days:
                    result.setdefault(faculty_of_group(g[2], faculty), {}).setdefault(g[2], {}).setdefault(d.isoformat(), [])

            # --- 4. пополняем словарь названий и разбираем содержимое
            content = {c: t for c, t in texts.items()
                       if t and c[0] >= time_x1 - 1 and c[1] >= first_top - 1}
            _learn(content.values())
            for c, raw in content.items():
                c_slots = [s_ for s_ in slots if _overlap(c[1], c[3], s_[0], s_[1]) > 0.5 * (s_[1] - s_[0])]
                c_groups = [g for g in groups if _overlap(c[0], c[2], g[0], g[1]) > 0.5 * (g[1] - g[0])]
                if not c_slots or not c_groups:
                    continue
                entries = parse_cell(raw)
                for s_ in c_slots:
                    mid = (s_[0] + s_[1]) / 2
                    day = next((d for t0, t1, d in days if t0 <= mid < t1), None)
                    if not day and carry_day and mid < first_day_top:
                        day = carry_day     # верх страницы — продолжение дня с предыдущей страницы
                    if not day:
                        continue
                    for g in c_groups:
                        bucket = result.setdefault(faculty_of_group(g[2], faculty), {}).setdefault(g[2], {}).setdefault(day.isoformat(), [])
                        for e in entries:
                            bucket.append({"pair": pair_no[s_[2]], "time": s_[2], **e})
            if days:
                carry_day = days[-1][2]
    for groups_ in result.values():
        for days_ in groups_.values():
            for lst in days_.values():
                lst.sort(key=lambda x: x["pair"])
    return result, specs


def pdf_date(path: Path) -> str:
    """Дата создания/изменения PDF из его свойств (например «D:20261029073733Z») — чтобы понять, какой файл свежее."""
    try:
        with pdfplumber.open(path) as pdf:
            meta = pdf.metadata or {}
        return str(meta.get("ModDate") or meta.get("CreationDate") or "")
    except Exception:
        return ""


def apply_days(result: dict, data: dict) -> None:
    """Кладёт разобранный PDF в общий результат. Если какой-то день группы уже пришёл из другого PDF,
    день заменяется целиком (а не склеивается), иначе две версии расписания дали бы лишние пары."""
    for faculty, groups in data.items():
        for group, days in groups.items():
            result.setdefault(faculty, {}).setdefault(group, {}).update(days)


def fetch_and_parse(form: str = "day") -> dict:
    """Скачивает и разбирает все PDF страницы выбранной формы обучения ("day" или "ext").
    Страницы ОДНОГО PDF склеиваются в parse_pdf(). Если один и тот же день группы есть в нескольких PDF,
    побеждает более свежий файл (по дате в свойствах PDF; при равенстве — тот, что ниже на странице сайта)."""
    links = find_pdf_links(form)
    if not links:
        raise RuntimeError(f"На странице ({URLS[form]}) нет ссылок на PDF (сайт изменился?)")
    parsed = []
    all_specs: dict[str, str] = {}
    for order, (faculty, label, url) in enumerate(links):
        try:
            path = download_pdf(url, form)
            data, specs = parse_pdf(path, faculty, label)
            all_specs.update(specs)
        except NotImplementedError:
            raise
        except Exception:
            logger.exception("Не удалось обработать %s — пропускаю", url)
            continue
        if not data:
            logger.warning("PDF не распознан (другая вёрстка?): %s | %s", label, url)
            continue
        parsed.append((pdf_date(path), order, data))
    result: dict = {}
    for _, _, data in sorted(parsed, key=lambda x: (x[0], x[1])):   # старые первыми — свежие перезаписывают
        apply_days(result, data)
    if all_specs:
        save_group_specs(all_specs)
        logger.info("Специализации групп обновлены: %s", ", ".join(f"{g} — {s}" for g, s in sorted(all_specs.items())))
    return result
