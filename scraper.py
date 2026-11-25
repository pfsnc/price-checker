import base64
import json
import logging
import os
import random
import re
import time
import unicodedata
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
logger = logging.getLogger("dual_source_stamp_scraper")

BASE_DIR = Path(__file__).resolve().parent
DATA_ENC_PATH = BASE_DIR / "docs" / "data.enc"

CNGOLD_LIST_URLS = [
    "https://m.cngold.org/cang/lm3790/",
    "https://m.cngold.org/cang/lm2992/",
]
SC5151_INDEX_URL = "http://www.5151sc.com/shougou.html"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

REQUEST_TIMEOUT = 15
PBKDF2_ITERATIONS = 100_000
PRICE_FIELDS = (
    "site_a_mint",
    "site_a_cto",
    "site_a_used",
    "site_b_mint",
    "site_b_cto",
    "site_b_used",
)

# 強制歸類為「其他」的專區頁面關鍵字（不可被內部的 J/T 編號覆寫分類）
FORCE_OTHER_KEYWORDS = ("小型张", "小本票", "普航欠改")

# 分類正則規則（依精確度排序）
CATEGORY_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("文", re.compile(r"文革|全国山河一片红|黑题词|全面胜利万岁|(?:^|[\s\d.])文\s*(?:\d{1,2}(?:-\d{1,2})?|[一二三四五六七八九十]{1,3})")),
    ("特", re.compile(r"特种邮票|特字|(?:^|\s)特\s*\d+")),
    ("紀", re.compile(r"纪念邮票|老纪特|纪字|紀字|(?:^|\s)[纪紀]\s*\d+")),
    ("J", re.compile(r"J字|J票|(?<![A-Za-z])J(?:\s*字|\s*\d+)", re.IGNORECASE)),
    ("T", re.compile(r"T字|T票|(?<![A-Za-z])T(?:\s*字|\s*\d+)", re.IGNORECASE)),
    ("編號", re.compile(r"编号邮票|^\d{1,2}\s*[-—]\s*\d{1,2}$")),
    ("生肖", re.compile(r"生肖|[一二三四]轮.*(?:大版|猴|鸡|狗|猪|鼠|牛|虎|兔|龙|蛇|马|羊)")),
    ("年冊", re.compile(r"年册|年冊")),
]

ZODIAC_ANIMALS = "鼠牛虎兔龙蛇马羊猴鸡狗猪"
CN_NUM_MAP = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


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
    salt = os.urandom(16)
    iv = os.urandom(12)
    key = _derive_aes_key(password, salt, PBKDF2_ITERATIONS)

    plaintext = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ciphertext = AESGCM(key).encrypt(iv, plaintext, None)

    envelope = {
        "v": 2,
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


# ==========================================
# 實體去重與名稱／志號正規化 (Entity Reconciliation)
# ==========================================

def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").strip()


def _cn_wen_to_digit(cn_str: str) -> str:
    """將「文一」~「文十九」轉換為「文1」~「文19」。"""
    if cn_str.isdigit():
        return str(int(cn_str))
    if cn_str == "十":
        return "10"
    if cn_str.startswith("十") and len(cn_str) == 2 and cn_str[1] in CN_NUM_MAP:
        return str(10 + CN_NUM_MAP[cn_str[1]])
    if len(cn_str) == 1 and cn_str in CN_NUM_MAP:
        return str(CN_NUM_MAP[cn_str])
    return cn_str


def _is_other_special_item(text: str) -> bool:
    """判斷是否屬於小型張、小本票、普航欠改等應固定歸於「其他」之品項。"""
    return bool(re.search(r"小型张|小全张|小本票|普/R|欠资|军邮|改值|航空|SB\d+", text, re.IGNORECASE))


def _detect_category(text: str) -> str:
    cleaned = _nfkc(text)
    if any(k in cleaned for k in FORCE_OTHER_KEYWORDS):
        return "其他"
    for category, pattern in CATEGORY_PATTERNS:
        if pattern.search(cleaned):
            return category
    return "其他"


def _extract_variant_tag(name: str) -> str:
    n = _nfkc(name)
    tags: List[str] = []
    if re.search(r"东贴|东北贴", n):
        tags.append("东贴")
    elif "东再" in n:
        tags.append("东再")
    if re.search(r"[\(（]原[\)）]|原版", n):
        tags.append("原")
    elif re.search(r"[\(（]再[\)）]|再版", n):
        tags.append("再")
    if "无齿" in n:
        tags.append("无齿")
    if "小型张" in n or "型张" in n:
        tags.append("小型张")
    elif "小全张" in n:
        tags.append("小全张")
    if "小本票" in n:
        tags.append("小本票")
    if "大版" in n:
        tags.append("大版")
    elif "小版" in n:
        tags.append("小版")
    return "+".join(tags)


def _normalize_code_and_name(category: str, raw_code: str, raw_name: str) -> Tuple[str, str]:
    """
    統一雙資料源的志號與名稱：
    - 支援拆解「文1语录」「文 1 战无不胜」「文一金边」「SB1咕咚」「普/R1...」「P1...」等內嵌志號。
    """
    code = re.sub(r"\s+", "", _nfkc(raw_code))
    name = re.sub(r"\s+", " ", _nfkc(raw_name))

    if not code:
        m = re.match(
            r"^("
            r"文\s*\d+(?:-\d+)?|"
            r"文[一二三四五六七八九十]{1,3}|"
            r"特\s*\d+|"
            r"[纪紀]\s*\d+|"
            r"J\s*\d+|"
            r"T\s*\d+|"
            r"SB\s*\d+|"
            r"普/R\d+[A-Za-z甲乙]?|"
            r"R\d+(?:/普)?|"
            r"P\d+"
            r")\s*[.、:：\-—]?\s*(.*)$",
            name,
            re.IGNORECASE,
        )
        if m:
            code = re.sub(r"\s+", "", m.group(1))
            rest = m.group(2).strip(" .、:：-—")
            name = rest if rest else code

    if code:
        # 轉換「文一」->「文1」
        m_wen_cn = re.match(r"^文([一二三四五六七八九十]{1,3})$", code)
        if m_wen_cn:
            code = f"文{_cn_wen_to_digit(m_wen_cn.group(1))}"

        code = re.sub(r"^纪", "紀", code)
        if re.match(r"^(?:j|t|sb|c|s|p|r)\d+", code, re.IGNORECASE):
            m_alpha = re.match(r"^([a-zA-Z]+)(.*)$", code)
            if m_alpha:
                code = m_alpha.group(1).upper() + m_alpha.group(2)

        if category in {"特", "紀", "J", "T"} and not re.match(r"^[特紀JT]", code):
            code = f"{category}{code}"
        if category == "文" and re.match(r"^\d+", code):
            code = f"文{code}"

    return code, name


def _build_canonical_id(category: str, code: str, name: str) -> str:
    variant = _extract_variant_tag(name)
    if code:
        base = f"{category}::{code}"
        return f"{base}::{variant}" if variant else base

    clean = re.sub(r"[\s\W_]+", "", _nfkc(name))
    if category == "生肖":
        ym = re.search(r"(19\d{2}|20\d{2})", clean)
        am = re.search(f"([{ZODIAC_ANIMALS}])", clean)
        if ym and am:
            base = f"生肖::{ym.group(1)}{am.group(1)}"
            return f"{base}::{variant}" if variant else base

    return f"{category}::{clean}"


def _migrate_existing_store(store: Dict[str, Any]) -> None:
    """自動修復與正規化舊資料（含文1、文2補歸類與保留小型張/小本票/普航欠改於其他分類）。"""
    old_stamps = store.get("stamps", {})
    migrated: Dict[str, Any] = {}

    for _, entry in old_stamps.items():
        raw_cat = entry.get("category", "其他")
        raw_cat = "紀" if raw_cat == "纪" else ("年冊" if raw_cat == "年册" else raw_cat)
        raw_code = entry.get("code", "")
        raw_name = entry.get("name", "")

        if raw_cat == "其他" and not _is_other_special_item(f"{raw_code} {raw_name}"):
            cat = _detect_category(f"{raw_code} {raw_name}")
        else:
            cat = raw_cat

        code, name = _normalize_code_and_name(cat, raw_code, raw_name)
        if cat == "其他" and not _is_other_special_item(f"{code} {name}"):
            cat = _detect_category(f"{code} {name}")
            code, name = _normalize_code_and_name(cat, code, name)

        canon_id = _build_canonical_id(cat, code, name)

        converted_hist: List[Dict[str, Any]] = []
        for h in entry.get("history", []):
            if "site_a_mint" in h or "site_b_mint" in h:
                point = {
                    "date": h.get("date", ""),
                    "ts": h.get("ts", ""),
                    **{f: h.get(f) for f in PRICE_FIELDS},
                }
            else:
                point = {
                    "date": h.get("date", ""),
                    "ts": h.get("ts", ""),
                    "site_a_mint": h.get("mint"),
                    "site_a_cto": h.get("cto"),
                    "site_a_used": h.get("used"),
                    "site_b_mint": None,
                    "site_b_cto": None,
                    "site_b_used": None,
                }
            converted_hist.append(point)

        if canon_id in migrated:
            by_date = {p["date"]: p for p in migrated[canon_id]["history"]}
            for p in converted_hist:
                if p["date"] in by_date:
                    for f in PRICE_FIELDS:
                        if by_date[p["date"]].get(f) is None and p.get(f) is not None:
                            by_date[p["date"]][f] = p[f]
                else:
                    by_date[p["date"]] = p
            migrated[canon_id]["history"] = sorted(by_date.values(), key=lambda x: x["date"])
        else:
            migrated[canon_id] = {
                "id": canon_id,
                "code": code,
                "name": name,
                "category": cat,
                "last_checked": entry.get("last_checked", ""),
                "history": converted_hist,
            }

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
        _migrate_existing_store(store)
        return store
    except Exception as exc:
        raise RuntimeError(
            f"無法解密現有 {DATA_ENC_PATH}（請確認 ENCRYPT_PASSWORD 是否與先前一致）: {exc}"
        ) from exc


# ==========================================
# 共用 HTTP 與表格解析引擎
# ==========================================

def _polite_sleep() -> None:
    """強制請求間隔 2 至 3 秒，防止對目標伺服器造成負擔。"""
    time.sleep(random.uniform(2.0, 3.0))


def _fetch_html(session: requests.Session, url: str, referer: str = "") -> Optional[str]:
    req_headers = dict(HEADERS)
    if referer:
        req_headers["Referer"] = referer
    try:
        resp = session.get(url, headers=req_headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        enc = resp.apparent_encoding or "utf-8"
        if enc.lower() in ("gb2312", "gbk"):
            enc = "gb18030"
        resp.encoding = enc
        return resp.text
    except requests.RequestException as exc:
        logger.warning("請求失敗，已安全跳過 [%s]: %s", url, exc)
        return None


def _extract_record_date(text: str) -> str:
    m1 = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text)
    if m1:
        return f"{int(m1.group(1)):04d}-{int(m1.group(2)):02d}-{int(m1.group(3)):02d}"
    m2 = re.search(r"(?:发布时间|更新时间)[：:\s]*(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m2:
        return f"{int(m2.group(1)):04d}-{int(m2.group(2)):02d}-{int(m2.group(3)):02d}"
    return datetime.now().strftime("%Y-%m-%d")


def _parse_price(raw_text: str) -> Optional[float]:
    text = _nfkc(raw_text)
    if not text or all(ch in "-—/\\暂无待定议价电 " for ch in text):
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


def _classify_header(col_text: str) -> str:
    h = re.sub(r"\s+", "", _nfkc(col_text))
    # 必須優先排除「邮票类别」「发行量」「发行日期」「序号」等非價格與非名稱欄
    if any(k in h for k in ("类别", "发行量", "发行日期", "备注")) or h == "序号":
        return "ignore"
    if "编号" in h or "志号" in h:
        return "code"
    if "名称" in h or "邮票" in h or "品种" in h or "生肖" in h or "品名" in h:
        return "name"
    if "盖销" in h:
        return "cto"
    if "信销" in h or "旧票" in h:
        return "used"
    if any(k in h for k in ("新票", "收价", "征价", "单枚", "单版", "收购", "价格")):
        return "mint"
    if any(k in h for k in ("整版", "原包")):
        return "mint_alt"
    return "unknown"


def _find_header_row(rows: List[Tag]) -> Tuple[int, List[str]]:
    for idx in range(min(3, len(rows))):
        cells = [c.get_text(" ", strip=True) for c in rows[idx].find_all(["th", "td"])]
        if len(cells) < 2:
            continue
        roles = [_classify_header(c) for c in cells]
        if any(r in ("name", "code", "mint", "cto", "used") for r in roles):
            if idx + 1 < len(rows):
                next_cells = [c.get_text(" ", strip=True) for c in rows[idx + 1].find_all(["th", "td"])]
                next_roles = [_classify_header(c) for c in next_cells]
                if len(next_cells) >= len(cells) and any(r in ("mint", "mint_alt") for r in next_roles):
                    return idx + 1, ["name"] + next_roles[1:] if next_roles[0] == "unknown" else next_roles
            return idx, roles
    return 0, []


def _split_column_groups(roles: List[str]) -> List[Tuple[int, List[str]]]:
    """
    智能切分單表與多欄並排表格（支援 6 欄 J/T 表、8 欄老紀特表、6 欄小本票表等）。
    """
    col_count = len(roles)
    anchor_role = "code" if roles.count("code") >= 2 else ("name" if roles.count("name") >= 2 else None)
    if anchor_role:
        starts = [i for i, r in enumerate(roles) if r == anchor_role]
        groups: List[Tuple[int, List[str]]] = []
        for k, start in enumerate(starts):
            end = starts[k + 1] if k + 1 < len(starts) else col_count
            groups.append((start, roles[start:end]))
        return groups
    return [(0, roles)]


def _parse_table(
    table: Tag,
    site_key: str,
    page_category: str,
    record_date: str,
    force_category_other: bool = False,
    tag_suffix: str = "",
) -> List[Dict[str, Any]]:
    rows = table.find_all("tr")
    if len(rows) < 2:
        return []

    hdr_idx, roles = _find_header_row(rows)
    if not roles:
        return []

    groups = _split_column_groups(roles)
    results: List[Dict[str, Any]] = []

    for tr in rows[hdr_idx + 1 :]:
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if not cells:
            continue

        for start, grp_roles in groups:
            grp_len = len(grp_roles)
            if start + grp_len > len(cells):
                continue
            chunk = cells[start : start + grp_len]

            raw_code = ""
            raw_name = ""
            mint_price: Optional[float] = None
            mint_alt_price: Optional[float] = None
            cto_price: Optional[float] = None
            used_price: Optional[float] = None

            for role, val in zip(grp_roles, chunk):
                if role == "code":
                    raw_code = val
                elif role == "name":
                    raw_name = val
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

            clean_check = re.sub(r"\s+", "", _nfkc(raw_name))
            if not clean_check or clean_check in ("名称", "邮票名称", "编号", "品种", "名称（老本）", "名称（新本）"):
                continue
            if mint_price is None and cto_price is None and used_price is None:
                continue

            if force_category_other:
                row_cat = "其他"
            else:
                row_cat = page_category if page_category != "其他" else _detect_category(f"{raw_code} {raw_name}")

            code, name = _normalize_code_and_name(row_cat, raw_code, raw_name)
            if not name:
                continue

            if not force_category_other and row_cat == "其他":
                row_cat = _detect_category(f"{code} {name}")
                code, name = _normalize_code_and_name(row_cat, code, name)

            if tag_suffix and tag_suffix not in name:
                name = f"{name}（{tag_suffix}）"

            results.append(
                {
                    "site": site_key,
                    "id": _build_canonical_id(row_cat, code, name),
                    "code": code,
                    "name": name,
                    "category": row_cat,
                    "mint": mint_price,
                    "cto": cto_price,
                    "used": used_price,
                    "date": record_date,
                }
            )

    return results


# ==========================================
# 資料源 A：金投收藏網 (m.cngold.org)
# ==========================================

def _extract_cngold_title(soup: BeautifulSoup, fallback: str) -> str:
    for h1 in soup.find_all("h1"):
        txt = h1.get_text(" ", strip=True)
        if txt and "金投网" not in txt and len(txt) > 6:
            return txt
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    return fallback


def scrape_site_a(session: requests.Session, per_source_limit: int = 10) -> Tuple[List[Dict[str, Any]], int]:
    """爬取資料源 A：金投收藏網 lm3790（文革/特種/J字/T字/年冊）與 lm2992（生肖）。"""
    seen_urls = set()
    articles: List[Tuple[str, str]] = []

    for idx, list_url in enumerate(CNGOLD_LIST_URLS):
        if idx > 0:
            _polite_sleep()
        html = _fetch_html(session, list_url, referer="https://m.cngold.org/cang/")
        if not html:
            continue

        source_count = 0
        try:
            soup = BeautifulSoup(html, "html.parser")
            for a_tag in soup.find_all("a", href=True):
                full_url = urljoin(list_url, a_tag["href"].strip())
                parsed = urlparse(full_url)
                if not parsed.netloc.endswith("cngold.org"):
                    continue
                if not re.search(r"/cang/(?:xw|jg)\d+\.html$", parsed.path):
                    continue

                title = a_tag.get("title") or a_tag.get_text(" ", strip=True)
                if "邮票" not in title or not any(k in title for k in ("价格", "行情", "报价", "收价")):
                    continue
                if full_url in seen_urls:
                    continue

                seen_urls.add(full_url)
                articles.append((full_url, title))
                source_count += 1
                if source_count >= per_source_limit:
                    break
        except Exception as exc:
            logger.error("[Site A] 解析列表失敗 [%s]: %s", list_url, exc)

    records: List[Dict[str, Any]] = []
    processed = 0
    for url, list_title in articles:
        _polite_sleep()
        html = _fetch_html(session, url, referer="https://m.cngold.org/cang/")
        if not html:
            continue

        try:
            soup = BeautifulSoup(html, "html.parser")
            article_title = _extract_cngold_title(soup, list_title)
            combined = f"{article_title} {list_title}"
            category = _detect_category(combined)
            record_date = _extract_record_date(combined)

            page_rows: List[Dict[str, Any]] = []
            for tbl in soup.find_all("table"):
                page_rows.extend(_parse_table(tbl, "site_a", category, record_date))

            logger.info("[Site A] %s -> 分類:[%s] 日期:[%s] %d 筆", article_title[:24], category, record_date, len(page_rows))
            records.extend(page_rows)
            processed += 1
        except Exception as exc:
            logger.warning("[Site A] 解析異常已跳過 [%s]: %s", url, exc)

    return records, processed


# ==========================================
# 資料源 B：點購收藏網 (www.5151sc.com)
# ==========================================

def scrape_site_b(session: requests.Session, max_pages: int = 20) -> Tuple[List[Dict[str, Any]], int]:
    """
    爬取資料源 B：點購收藏網 shougou.html 及其全系列子頁面：
    紀、特、文、編號、J 字、T 字、生肖、年冊，以及小型張、小本票、普航欠改（歸類於「其他」）。
    """
    index_html = _fetch_html(session, SC5151_INDEX_URL, referer="http://www.5151sc.com/")
    if not index_html:
        return [], 0

    include_kw = (
        "纪念邮票", "特种邮票", "文革邮票", "编号邮票",
        "J字邮票", "T字邮票", "J票", "T票",
        "小型张", "小本票", "普航欠改",
        "十二生肖", "三轮生肖", "二轮生肖", "年册",
    )
    exclude_kw = ("纸币", "人民币", "金银", "银元", "硬币", "磁卡", "IC卡", "章程", "资讯", "视频", "大版票", "小版票")

    seen_urls = set()
    subpages: List[Tuple[str, str]] = []
    try:
        soup = BeautifulSoup(index_html, "html.parser")
        for a_tag in soup.find_all("a", href=True):
            full_url = urljoin(SC5151_INDEX_URL, a_tag["href"].strip())
            parsed = urlparse(full_url)
            if not parsed.netloc.endswith("5151sc.com"):
                continue
            if not parsed.path.endswith(".html") or parsed.path == "/shougou.html":
                continue

            link_text = a_tag.get_text(" ", strip=True)
            if any(ex in link_text for ex in exclude_kw):
                continue
            if not any(inc in link_text for inc in include_kw):
                continue
            if full_url in seen_urls:
                continue

            seen_urls.add(full_url)
            subpages.append((full_url, link_text))
            if len(subpages) >= max_pages:
                break
    except Exception as exc:
        logger.error("[Site B] 解析首頁失敗: %s", exc)
        return [], 0

    records: List[Dict[str, Any]] = []
    processed = 0
    for url, link_title in subpages:
        _polite_sleep()
        html = _fetch_html(session, url, referer=SC5151_INDEX_URL)
        if not html:
            continue

        try:
            soup = BeautifulSoup(html, "html.parser")
            h1 = soup.find("h1")
            page_title = h1.get_text(" ", strip=True) if h1 else (soup.title.string.strip() if soup.title and soup.title.string else link_title)
            body_text = soup.get_text(" ", strip=True)[:1200]
            title_combo = f"{page_title} {link_title}"

            # 判斷是否為「小型張 / 小本票 / 普航欠改」專區 -> 鎖定歸類於「其他」
            force_other = any(k in title_combo for k in FORCE_OTHER_KEYWORDS)
            tag_suffix = ""
            if "小型张" in title_combo:
                tag_suffix = "小型张"
            elif "小本票" in title_combo:
                tag_suffix = "小本票"

            category = "其他" if force_other else _detect_category(title_combo)
            record_date = _extract_record_date(f"{page_title} {body_text}")

            page_rows: List[Dict[str, Any]] = []
            for tbl in soup.find_all("table"):
                page_rows.extend(
                    _parse_table(
                        tbl,
                        site_key="site_b",
                        page_category=category,
                        record_date=record_date,
                        force_category_other=force_other,
                        tag_suffix=tag_suffix,
                    )
                )

            logger.info("[Site B] %s -> 分類:[%s] 日期:[%s] %d 筆", page_title[:24], category, record_date, len(page_rows))
            records.extend(page_rows)
            processed += 1
        except Exception as exc:
            logger.warning("[Site B] 解析異常已跳過 [%s]: %s", url, exc)

    return records, processed


# ==========================================
# 跨站合併與 Smart Diff 寫入
# ==========================================

def apply_dual_source_smart_diff(
    store: Dict[str, Any],
    all_records: List[Dict[str, Any]],
    checked_at: str,
) -> Tuple[int, int]:
    stamps_map: Dict[str, Any] = store.setdefault("stamps", {})
    grouped: Dict[str, Dict[str, Dict[str, Any]]] = {}
    meta_by_id: Dict[str, Dict[str, str]] = {}

    for rec in all_records:
        cid = rec["id"]
        dt = rec["date"]
        if cid not in meta_by_id or len(rec["name"]) > len(meta_by_id[cid]["name"]):
            meta_by_id[cid] = {
                "code": rec["code"],
                "name": rec["name"],
                "category": rec["category"],
            }

        bucket = grouped.setdefault(cid, {}).setdefault(
            dt,
            {
                "site_a_seen": False,
                "site_b_seen": False,
                "site_a_mint": None,
                "site_a_cto": None,
                "site_a_used": None,
                "site_b_mint": None,
                "site_b_cto": None,
                "site_b_used": None,
            },
        )
        prefix = rec["site"]
        bucket[f"{prefix}_seen"] = True
        for cond in ("mint", "cto", "used"):
            if rec[cond] is not None:
                bucket[f"{prefix}_{cond}"] = rec[cond]

    appended = 0
    for cid, date_map in grouped.items():
        meta = meta_by_id[cid]
        entry = stamps_map.get(cid)
        if entry is None:
            entry = {
                "id": cid,
                "code": meta["code"],
                "name": meta["name"],
                "category": meta["category"],
                "last_checked": checked_at,
                "history": [],
            }
            stamps_map[cid] = entry
        else:
            if len(meta["name"]) > len(entry.get("name", "")):
                entry["name"] = meta["name"]
            entry["last_checked"] = checked_at

        history: List[Dict[str, Any]] = entry["history"]
        for dt in sorted(date_map.keys()):
            day_data = date_map[dt]
            last_point = history[-1] if history else None

            if last_point and dt < last_point["date"]:
                continue

            new_point = {"date": dt, "ts": checked_at}
            for site in ("site_a", "site_b"):
                seen = day_data[f"{site}_seen"]
                for cond in ("mint", "cto", "used"):
                    field = f"{site}_{cond}"
                    if seen:
                        new_point[field] = day_data[field]
                    else:
                        new_point[field] = last_point.get(field) if last_point else None

            if not last_point:
                history.append(new_point)
                appended += 1
            else:
                changed = any(new_point[f] != last_point.get(f) for f in PRICE_FIELDS)
                if changed:
                    if dt == last_point["date"]:
                        last_point.update(new_point)
                    else:
                        history.append(new_point)
                    appended += 1

    store["last_checked"] = checked_at
    if appended > 0 or not store.get("updated_at"):
        store["updated_at"] = checked_at

    return appended, len(grouped)


def main() -> None:
    password = os.environ.get("ENCRYPT_PASSWORD", "").strip()
    if not password:
        raise RuntimeError("環境變數 ENCRYPT_PASSWORD 未設定，無法進行加密輸出。")

    store = load_existing_store(password)
    checked_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with requests.Session() as session:
        rec_a, proc_a = scrape_site_a(session, per_source_limit=10)
        _polite_sleep()
        rec_b, proc_b = scrape_site_b(session, max_pages=20)

    all_records = rec_a + rec_b
    logger.info(
        "雙站擷取摘要 -> Site A: %d 頁 / %d 筆；Site B: %d 頁 / %d 筆",
        proc_a, len(rec_a), proc_b, len(rec_b),
    )

    if not all_records:
        logger.warning("本次雙站皆未取得有效報價，保留既有加密檔不覆寫。")
        return

    appended, merged_count = apply_dual_source_smart_diff(store, all_records, checked_at)
    encrypted_output = encrypt_json_payload(store, password)

    DATA_ENC_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_ENC_PATH.write_text(encrypted_output, encoding="utf-8")

    logger.info(
        "去重合併完成！共 %d 款獨立郵票，新增/更新 %d 筆價格變動紀錄，已加密輸出至 %s",
        merged_count,
        appended,
        DATA_ENC_PATH,
    )


if __name__ == "__main__":
    main()
