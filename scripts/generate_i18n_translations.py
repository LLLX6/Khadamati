"""Generate static client translations for the checked-in five-language catalog.

This is a release helper, never called by the application at runtime. It uses the
public Microsoft Translator web endpoint, validates every marker, and writes a
deterministic JS catalog only after all three languages are complete.
"""

from __future__ import annotations

import concurrent.futures
import json
import re
import time
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "tmp" / "i18n-source.json"
OUTPUT_PATH = ROOT / "assets" / "scripts" / "khadamati-i18n-data.js"
CACHE_DIR = ROOT / "tmp" / "i18n-cache"
TARGETS = ("hi", "bn", "ur")
MAX_BATCH_CHARS = 950
MAX_WORKERS = 8


def batches(phrases: list[str]) -> list[list[tuple[int, str]]]:
    return indexed_batches(list(enumerate(phrases)))


def indexed_batches(entries: list[tuple[int, str]]) -> list[list[tuple[int, str]]]:
    output: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    current_size = 0
    for index, phrase in entries:
        entry_size = len(phrase) + 18
        if current and current_size + entry_size > MAX_BATCH_CHARS:
            output.append(current)
            current = []
            current_size = 0
        current.append((index, phrase))
        current_size += entry_size
    if current:
        output.append(current)
    return output


def translator_credentials() -> tuple[str, str, str, str, dict[str, str]]:
    session = requests.Session()
    response = session.get("https://www.bing.com/translator", timeout=30)
    response.raise_for_status()
    html = response.text
    ig = re.search(r'IG:"([A-F0-9]+)"', html)
    abuse = re.search(r'params_AbusePreventionHelper = \[(\d+),"([^"]+)",', html)
    iid = re.search(r'id="rich_tta" data-iid="([^"]+)"', html)
    if not (ig and abuse and iid):
        raise RuntimeError("Translator credentials were not found in the response.")
    return ig.group(1), iid.group(1), abuse.group(1), abuse.group(2), session.cookies.get_dict()


def parse_marked_translation(text: str, expected: list[tuple[int, str]]) -> dict[int, str]:
    matches = list(re.finditer(r"\[\[K(\d{5})\]\]", text))
    if len(matches) != len(expected):
        raise ValueError(f"Expected {len(expected)} markers, received {len(matches)}")
    translated: dict[int, str] = {}
    for offset, match in enumerate(matches):
        index = int(match.group(1))
        end = matches[offset + 1].start() if offset + 1 < len(matches) else len(text)
        value = text[match.end() : end].strip()
        if index != expected[offset][0] or not value:
            raise ValueError(f"Invalid translated segment for phrase {expected[offset][0]}")
        translated[index] = value
    return translated


def translate_batch(
    target: str,
    number: int,
    batch: list[tuple[int, str]],
    credentials: tuple[str, str, str, str, dict[str, str]],
) -> tuple[int, dict[int, str]]:
    ig, iid, key, token, cookies = credentials
    source = "\n".join(f"[[K{index:05d}]]\n{phrase}" for index, phrase in batch)
    url = f"https://www.bing.com/ttranslatev3?isVertical=1&IG={ig}&IID={iid}.{number + 1}"
    payload = {"fromLang": "en", "text": source, "to": target, "token": token, "key": key}
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            response = requests.post(url, data=payload, cookies=cookies, timeout=60)
            response.raise_for_status()
            body = response.json()
            text = body[0]["translations"][0]["text"]
            return number, parse_marked_translation(text, batch)
        except Exception as error:  # pragma: no cover - network release helper
            last_error = error
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Translation batch {target}/{number} failed: {last_error}")


def translate_language(target: str, phrases: list[str]) -> list[str]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{target}.json"
    source_cache_path = CACHE_DIR / "source.json"
    reusable: dict[str, str] = {}
    if cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if len(cached) == len(phrases) and all(isinstance(item, str) and item for item in cached):
            print(f"Using cached {target} catalog ({len(cached)} phrases).", flush=True)
            return cached
        if source_cache_path.exists() and isinstance(cached, list):
            cached_source = json.loads(source_cache_path.read_text(encoding="utf-8"))
            if len(cached_source) == len(cached):
                reusable = dict(zip(cached_source, cached))

    translated: list[str | None] = [reusable.get(phrase) for phrase in phrases]
    pending = [(index, phrase) for index, phrase in enumerate(phrases) if not translated[index]]
    if not pending:
        return [str(item) for item in translated]
    grouped = indexed_batches(pending)
    credentials = translator_credentials()
    print(f"Translating {target}: {len(pending)} new phrases in {len(grouped)} validated batches.", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [
            executor.submit(translate_batch, target, number, batch, credentials)
            for number, batch in enumerate(grouped)
        ]
        for completed, future in enumerate(concurrent.futures.as_completed(futures), 1):
            _, values = future.result()
            for index, value in values.items():
                translated[index] = value
            if completed % 10 == 0 or completed == len(futures):
                print(f"  {target}: {completed}/{len(futures)} batches", flush=True)

    if any(not item for item in translated):
        raise RuntimeError(f"The {target} catalog contains missing translations.")
    result = [str(item) for item in translated]
    cache_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def javascript_catalog(phrases: list[str], translations: dict[str, list[str]]) -> str:
    rows = [
        [phrase, translations["hi"][index], translations["bn"][index], translations["ur"][index]]
        for index, phrase in enumerate(phrases)
    ]
    data = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    return (
        "/* Generated static interface translations. No runtime translation service is used. */\n"
        "(function attachKhadamatiLocaleData(root){\n"
        "  'use strict';\n"
        f"  root.KHADAMATI_I18N_ROWS={data};\n"
        "})(typeof globalThis!=='undefined'?globalThis:this);\n"
    )


def main() -> None:
    phrases = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    translations = {target: translate_language(target, phrases) for target in TARGETS}
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / "source.json").write_text(json.dumps(phrases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(javascript_catalog(phrases, translations), encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH} with {len(phrases)} complete rows.", flush=True)


if __name__ == "__main__":
    main()
