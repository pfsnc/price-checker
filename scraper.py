import base64
import json
import logging
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("cngold_serverless_scraper")

BASE_DIR = Path(__file__).resolve().parent
DATA_ENC_PATH = BASE_DIR / "docs" / "data.enc"

TARGET_URLS = [
    "https://m.cngold.org/cang/lm3790/",
    "https://m.cngold.org/cang/lm2992/",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://m.cngold.org/cang/",
}

REQUEST_TIMEOUT = 15
PBKDF2_ITERATIONS = 100_000
PER_SOURCE_ARTICLE_LIMIT = 10

# 分類正則規則（依精確度排序）
CATEGORY_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("文", re.compile(r"文革|^文\s*\d+")),
    ("特", re.compile(r"特种邮票|特字|^特\s*\d+")),
    ("紀", re.compile(r"纪念邮票|老纪特|纪字|紀字|^[纪紀]\s*\d+")),
    ("J", re.compile(r"J字|(?<![A-Za-z])J(?:\s*字|\d+)", re.IGNORECASE)),
    ("T", re.compile(r"T字|(?<![A-Za-z])T(?:\s*字|\d+)", re.IGNORECASE)),
    ("生肖", re.compile(r"生肖|[一二三四]轮.*(?:大版|猴|鸡|狗|猪|鼠|牛|虎|兔|龙|蛇|马|羊)")),
    ("年冊", re.compile(r"年册|年冊")),
]


# ==========================================
# 加密 / 解密模組 (PBKDF2-SHA256 + AES-256-GCM)
# ==========================================

def _derive_aes_key(password: str, salt: bytes, iterations: int) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
    )
    return kdf.derive(password.encode("utf-8"))


def encrypt_json_payload(data: Dict[str, Any], password: str) -> str:
    """將資料序列化為 JSON 並以 AES-256-GCM 加密輸出。"""
    salt = os.urandom(16)
    iv = os.urandom(12)
    key = _derive_aes_key(password, salt, PBKDF2_ITERATIONS)

    plaintext = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ciphertext = AESGCM(key).encrypt(iv, plaintext, None)

    envelope = {
        "v": 1,
        "alg": "PBKDF2-SHA256-AES-256-GCM",
        "iter": PBKDF2_ITERATIONS,
        "salt": base64.b64encode(salt).decode("ascii"),
        "iv": base64.b64encode(iv).decode("ascii"),
        "ct": base64.b64encode(ciphertext).decode("ascii"),
    }
    return json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))


def decrypt_json_payload(raw_text: str, password: str) -> Dict[str, Any]:
    envelope = json.loads(raw_text)
    salt = base64.b64decode(envelope["salt"])
    iv = base64.b64decode(envelope["iv"])
    ct = base64.b64decode(envelope["ct"])
    iterations = int(envelope.get("iter", PBKDF2_ITERATIONS))

    key = _derive_aes_key(password, salt, iterations)
    plaintext = AESGCM(key).decrypt(iv, ct, None)
    return json.loads(plaintext.decode("utf-8"))


def _migrate_legacy_categories(store: Dict[str, Any]) -> None:
    """自動修復先前誤歸類為「其他」的舊紀錄，避免歷史資料殘留錯誤標籤。"""
    old_stamps = store.get("stamps", {})
    migrated: Dict[str, Any] = {}

    for key, entry in old_stamps.items():
        cat = entry.get("category", "其他")
        code = entry.get("code", "")
        name = entry.get("name", "")
        if cat == "其他":
            fixed_cat = _detect_category(f"{code} {name}")
            if fixed_cat != "其他":
                cat = fixed_cat
                entry["category"] = fixed_cat

        new_key = f"{cat}::{code}::{name}"
        entry["id"] = new_key
        if new_key in migrated:
            # 合併歷史紀錄並依日期排序去重
            existing_hist = {
                (h.get("date"), h.get("mint"), h.get("cto"), h.get("used")): h
                for h in migrated[new_key].get("history", [])
            }
            for h in entry.get("history", []):
                existing_hist[(h.get("date"), h.get("mint"), h.get("cto"), h.get("used"))] = h
            migrated[new_key]["history"] = sorted(
                existing_hist.values(), key=lambda x: (x.get("date", ""), x.get("ts", ""))
            )
        else:
            migrated[new_key] = entry

    store["stamps"] = migrated


def load_existing_store(password: str) -> Dict[str, Any]:
    if not DATA_ENC_PATH.exists():
        logger.info("找不到現有 %s，將建立全新資料集。", DATA_ENC_PATH)
        return {"updated_at": "", "last_checked": "", "stamps": {}}

    raw = DATA_ENC_PATH.read_text(encoding="utf-8")
    try:
        store = decrypt_json_payload(raw, password)
        if not isinstance(store, dict) or "stamps" not in store:
            raise ValueError("資料格式不符")
        _migrate_legacy_categories(store)
        return store
    except Exception as exc:
        raise RuntimeError(
            f"無法解密現有 {DATA_ENC_PATH}（請確認 ENCRYPT_PASSWORD 是否與先前一致）: {exc}"
        ) from exc


# ==========================================
# 網頁抓取與 BeautifulSoup 解析模組
# ==========================================

def _polite_sleep() -> None:
    """強制請求間隔 2 至 3 秒，防止對目標伺服器造成負擔。"""
    time.sleep(random.uniform(2.0, 3.0))


def _fetch_html(session: requests.Session, url: str) -> Optional[str]:
    """安全發送單次 HTTP GET 請求（嚴禁無限重試迴圈）。"""
    try:
        resp = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.text
    except requests.RequestException as exc:
        logger.warning("請求失敗，已安全跳過 [%s]: %s", url, exc)
        return None


def _extract_article_title(soup: BeautifulSoup, fallback: str) -> str:
    """
    正確擷取文章主標題，排除網站導覽列 Logo 的 <h1>（如「金投网-手机版」）。
    """
    for h1 in soup.find_all("h1"):
        txt = h1.get_text(" ", strip=True)
        if txt and "金投网" not in txt and len(txt) > 6:
            return txt

    if soup.title and soup.title.string:
        title_txt = soup.title.string.strip()
        if title_txt and "金投网" not in title_txt[:8]:
            return title_txt
        return title_txt

    return fallback


def _detect_category(text: str) -> str:
    """使用正則表達式識別郵票分類：特、紀、文、J、T、生肖、年冊。"""
    cleaned = text.strip()
    for category, pattern in CATEGORY_PATTERNS:
        if pattern.search(cleaned):
            return category
    return "其他"


def _extract_record_date(text: str) -> str:
    match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text)
    if match:
        y, m, d = int(match.group(1)), int(match.group(2)), int(match.group(3))
        return f"{y:04d}-{m:02d}-{d:02d}"
    return datetime.now().strftime("%Y-%m-%d")


def _parse_price(raw_text: str) -> Optional[float]:
    text = raw_text.strip()
    if not text or all(ch in "-—/\\暂无待定 " for ch in text):
        return None

    multiplier = 10000.0 if "万" in text else 1.0
    nums = re.findall(r"\d+(?:\.\d+)?", text)
    if not nums:
        return None

    try:
        values = [float(n) * multiplier for n in nums[:2]]
        price = sum(values) / len(values) if "-" in text and len(values) == 2 else values[0]
        return round(price, 2)
    except ValueError:
        return None


def _format_code(category: str, raw_code: str) -> str:
    code = raw_code.strip()
    if not code:
        return ""
    if category in {"特", "紀", "J", "T"} and not re.match(r"^[特纪紀JT]", code, re.IGNORECASE):
        return f"{category}{code}"
    return code


def _classify_header(col_text: str) -> str:
    h = col_text.strip()
    if "编号" in h or "志号" in h:
        return "code"
    if "名称" in h or "邮票" in h or "品种" in h or "生肖" in h:
        return "name"
    if "盖销" in h:
        return "cto"
    if "信销" in h:
        return "used"
    if any(k in h for k in ("新票", "收价", "征价", "单枚", "单版", "价格")):
        return "mint"
    if any(k in h for k in ("整版", "原包")):
        return "mint_alt"
    return "unknown"


def _parse_table(table: Tag, page_category: str, record_date: str) -> List[Dict[str, Any]]:
    rows = table.find_all("tr")
    if len(rows) < 2:
        return []

    header_cells = [c.get_text(strip=True) for c in rows[0].find_all(["th", "td"])]
    if not header_cells:
        return []

    roles = [_classify_header(h) for h in header_cells]
    col_count = len(roles)

    # 若首列表頭同時含名稱與生肖字樣（例如生肖表頭「一轮生肖邮票」），仍可輔助判斷分類
    header_joined = " ".join(header_cells)
    if page_category == "其他":
        page_category = _detect_category(header_joined)

    # 偵測 J字 / T字 雙欄並排表格
    if col_count >= 6 and col_count % 2 == 0 and roles[: col_count // 2] == roles[col_count // 2 :]:
        group_size = col_count // 2
        groups = [roles[:group_size], roles[group_size:]]
    else:
        groups = [roles]

    results: List[Dict[str, Any]] = []
    for tr in rows[1:]:
        cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
        if not cells:
            continue

        offset = 0
        for grp_roles in groups:
            grp_len = len(grp_roles)
            if offset + grp_len > len(cells):
                break
            chunk = cells[offset : offset + grp_len]
            offset += grp_len

            raw_code = ""
            name = ""
            mint_price: Optional[float] = None
            mint_alt_price: Optional[float] = None
            cto_price: Optional[float] = None
            used_price: Optional[float] = None

            for role, val in zip(grp_roles, chunk):
                if role == "code":
                    raw_code = val
                elif role == "name":
                    name = val
                elif role == "mint" and mint_price is None:
                    mint_price = _parse_price(val)
                elif role == "mint_alt" and mint_alt_price is None:
                    mint_alt_price = _parse_price(val)
                elif role == "cto":
                    cto_price = _parse_price(val)
                elif role == "used":
                    used_price = _parse_price(val)

            if mint_price is None:
                mint_price = mint_alt_price

            if not name or name in ("名称", "邮票名称"):
                continue
            if mint_price is None and cto_price is None and used_price is None:
                continue

            # 若頁面分類為其他，再以郵票編號與名稱做精準正則補判
            row_category = page_category
            if row_category == "其他":
                row_category = _detect_category(f"{raw_code} {name}")

            results.append(
                {
                    "code": _format_code(row_category, raw_code),
                    "name": name,
                    "category": row_category,
                    "mint": mint_price,
                    "cto": cto_price,
                    "used": used_price,
                    "date": record_date,
                }
            )

    return results


def _collect_article_links(
    session: requests.Session,
    list_urls: List[str],
    per_source_limit: int = PER_SOURCE_ARTICLE_LIMIT,
) -> List[Tuple[str, str]]:
    """
    從 lm3790（/cang/jg*.html）與 lm2992（/cang/xw*.html）分別配額擷取行情連結，
    確保文革、特種、J字、T字、年冊與生肖系列皆不會互相排擠遺漏。
    """
    seen_urls = set()
    articles: List[Tuple[str, str]] = []

    for idx, list_url in enumerate(list_urls):
        if idx > 0:
            _polite_sleep()

        html = _fetch_html(session, list_url)
        if not html:
            continue

        source_count = 0
        try:
            soup = BeautifulSoup(html, "html.parser")
            for a_tag in soup.find_all("a", href=True):
                href = a_tag["href"].strip()
                full_url = urljoin(list_url, href)
                parsed = urlparse(full_url)

                if not parsed.netloc.endswith("cngold.org"):
                    continue
                # 同時支援 lm3790 的 /cang/jg*.html 與 lm2992 的 /cang/xw*.html
                if not re.search(r"/cang/(?:xw|jg)\d+\.html$", parsed.path):
                    continue

                title = a_tag.get("title") or a_tag.get_text(" ", strip=True)
                if "邮票" not in title:
                    continue
                if not any(k in title for k in ("价格", "行情", "报价", "收价")):
                    continue
                if full_url in seen_urls:
                    continue

                seen_urls.add(full_url)
                articles.append((full_url, title))
                source_count += 1
                if source_count >= per_source_limit:
                    break
        except Exception as exc:
            logger.error("解析列表頁失敗 [%s]: %s", list_url, exc)

    return articles


# ==========================================
# Smart Diff 合併與加密寫出
# ==========================================

def apply_smart_diff(
    store: Dict[str, Any],
    scraped_records: List[Dict[str, Any]],
    checked_at: str,
) -> Tuple[int, int]:
    stamps_map: Dict[str, Any] = store.setdefault("stamps", {})
    appended_count = 0
    checked_count = 0

    ordered_records = sorted(scraped_records, key=lambda r: r["date"])

    for rec in ordered_records:
        key = f"{rec['category']}::{rec['code']}::{rec['name']}"
        entry = stamps_map.get(key)

        if entry is None:
            entry = {
                "id": key,
                "code": rec["code"],
                "name": rec["name"],
                "category": rec["category"],
                "last_checked": checked_at,
                "history": [],
            }
            stamps_map[key] = entry

        entry["last_checked"] = checked_at
        checked_count += 1

        history: List[Dict[str, Any]] = entry["history"]
        new_prices = (rec["mint"], rec["cto"], rec["used"])

        if not history:
            history.append(
                {
                    "date": rec["date"],
                    "ts": checked_at,
                    "mint": rec["mint"],
                    "cto": rec["cto"],
                    "used": rec["used"],
                }
            )
            appended_count += 1
            continue

        last_point = history[-1]
        if rec["date"] < last_point["date"]:
            continue

        last_prices = (last_point.get("mint"), last_point.get("cto"), last_point.get("used"))
        if new_prices != last_prices:
            history.append(
                {
                    "date": rec["date"],
                    "ts": checked_at,
                    "mint": rec["mint"],
                    "cto": rec["cto"],
                    "used": rec["used"],
                }
            )
            appended_count += 1

    store["last_checked"] = checked_at
    if appended_count > 0 or not store.get("updated_at"):
        store["updated_at"] = checked_at

    return appended_count, checked_count


def main() -> None:
    password = os.environ.get("ENCRYPT_PASSWORD", "").strip()
    if not password:
        raise RuntimeError("環境變數 ENCRYPT_PASSWORD 未設定，無法進行加密輸出。")

    store = load_existing_store(password)
    checked_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    all_records: List[Dict[str, Any]] = []
    processed = 0

    with requests.Session() as session:
        article_links = _collect_article_links(session, TARGET_URLS)
        logger.info("共探索到 %d 篇郵票行情文章。", len(article_links))

        for url, list_title in article_links:
            _polite_sleep()
            html = _fetch_html(session, url)
            if not html:
                continue

            try:
                soup = BeautifulSoup(html, "html.parser")
                article_title = _extract_article_title(soup, list_title)
                combined_title = f"{article_title} {list_title}"

                category = _detect_category(combined_title)
                record_date = _extract_record_date(combined_title)

                page_records: List[Dict[str, Any]] = []
                for tbl in soup.find_all("table"):
                    page_records.extend(_parse_table(tbl, category, record_date))

                logger.info(
                    "文章 [%s] -> 分類:[%s] 日期:[%s] 解析得 %d 筆",
                    article_title[:28],
                    category,
                    record_date,
                    len(page_records),
                )
                all_records.extend(page_records)
                processed += 1
            except Exception as exc:
                logger.exception("解析文章異常，已安全跳過 [%s]: %s", url, exc)

    if processed == 0 or not all_records:
        logger.warning("本次未成功擷取任何有效報價（processed=%d），保留既有加密檔不覆寫。", processed)
        return

    appended, checked = apply_smart_diff(store, all_records, checked_at)
    encrypted_output = encrypt_json_payload(store, password)

    DATA_ENC_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_ENC_PATH.write_text(encrypted_output, encoding="utf-8")

    logger.info(
        "完成！檢查 %d 筆郵票，新增 %d 筆價格變動紀錄，已 AES-GCM 加密輸出至 %s",
        checked,
        appended,
        DATA_ENC_PATH,
    )


if __name__ == "__main__":
    main()
