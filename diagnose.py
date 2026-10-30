"""Необязательно. Показывает, что отдаёт сайт. Запуск:
    python diagnose.py        — дневное обучение
    python diagnose.py ext    — заочное обучение
Создаёт diagnose_output.txt — его содержимое можно прислать Claude."""
import io
import sys
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

import parser_site

FORM = "ext" if len(sys.argv) > 1 and sys.argv[1].lower() in ("ext", "zaochnoe", "заочное") else "day"
URL = parser_site.URLS[FORM]
H = {"User-Agent": "Mozilla/5.0 (compatible; ScheduleBot/1.0)"}
out = []


def log(*a):
    line = " ".join(map(str, a))
    print(line)
    out.append(line)


r = requests.get(URL, headers=H, timeout=20)
log("Форма:", FORM, "|", URL)
log("Статус:", r.status_code, "| размер:", len(r.text))
soup = BeautifulSoup(r.text, "html.parser")
log("Таблиц в HTML:", len(soup.find_all("table")))
links = parser_site.find_pdf_links(FORM)
log(f"PDF-ссылок: {len(links)}\n")
by_fac = {}
for fac, label, url in links:
    by_fac.setdefault(fac, []).append((label, url))
for fac, items in by_fac.items():
    log(f"== {fac} ({len(items)} ссылок)")
    for label, url in items:
        log("  -", repr(label), url)
log("\n=== Проверка разбора каждого PDF ===")
for fac, label, url in links:
    try:
        res = parser_site.parse_pdf(parser_site.download_pdf(url, FORM), fac, label)
        gs = [g for x in res.values() for g in x]
        log(f"- {fac} | {label}: групп {len(gs)}: {', '.join(gs)}" if gs else f"- {fac} | {label}: НЕ РАСПОЗНАН")
    except Exception as e:
        log(f"- {fac} | {label}: ОШИБКА {e}")
if links:
    import pdfplumber
    for fac, items in by_fac.items():           # по одному PDF от каждого факультета
        label, url = items[0]
        log(f"\n######## {fac} | {label} ########")
        with pdfplumber.open(io.BytesIO(requests.get(url, headers=H, timeout=60).content)) as pdf:
            log("Страниц:", len(pdf.pages))
            for n, p in enumerate(pdf.pages[:3], start=1):      # первые 3 страницы: видно, есть ли шапка на продолжениях
                log(f"--- СТРАНИЦА {n}: ТЕКСТ ---\n" + (p.extract_text() or "(пусто — возможно, скан)"))
                for t in p.extract_tables()[:2]:
                    log(f"--- СТРАНИЦА {n}: ТАБЛИЦА (первые 12 строк) ---")
                    for row in t[:12]:
                        log(row)
else:
    open(f"diagnose_page_{FORM}.html", "w", encoding="utf-8").write(r.text)
    log("PDF нет — HTML сохранён в diagnose_page_" + FORM + ".html")
open("diagnose_output.txt", "w", encoding="utf-8").write("\n".join(out))
print("\nГотово: diagnose_output.txt")
