import base64
import json
import logging
import math
import os
import random
import re
import tempfile
import time
import unicodedata
from datetime import datetime, timedelta
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

# ==========================================
# 集中管理：來源白名單、傳輸限制與資料閾值（可安全調整）
# ==========================================

# 主機白名單僅能防止 SSRF／惡意轉址，無法防範網域過期搶註或 HTTP 明文竄改。
# 點購網目前僅提供 HTTP，其回應內容一律視為不可信輸入，須通過嚴格驗證與 Sanity Check。
ALLOWED_HOST_POLICIES: Dict[str, Tuple[str, ...]] = {
    "m.cngold.org": ("https",),
    "www.5151sc.com": ("http", "https"),
}
ACTIVE_SITES = ("site_a", "site_b")

CNGOLD_URLS = [
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
MAX_REDIRECTS = 5
MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 單頁回應上限 5 MB
ALLOWED_CONTENT_TYPES = ("text/html", "application/xhtml+xml")

MIN_VALID_DATE = "2020-01-01"
MAX_FUTURE_DAYS = 1
MAX_PRICE_YUAN = 50_000_000.0  # 單筆報價合理上限（5000 萬元）
MAX_RECORDS_PER_PAGE = 1_000
MAX_RECORDS_PER_RUN = 5_000

# ==========================================
# Fail-Closed 資料合理性檢查 (Sanity Check) 門檻（全來源適用）
# 註：正常市場價格劇烈波動不予攔截，僅檢查結構與來源完整性。
# ==========================================
MIN_STAMPS_FOR_COUNT_CHECK = 20     # 既有品種數達此門檻才啟用比例檢查
MAX_STAMP_DROP_RATIO = 0.40         # 單一來源或總體覆蓋率減少超過 40% 視為異常或改版
MAX_STAMP_GROWTH_RATIO = 2.00       # 新增品種超過既有總數 200% 視為誤抓雜訊
MIN_FIELD_COUNT_FOR_RETENTION = 10  # 某來源某品相既有報價數達此門檻才檢查保留率
MIN_PRICE_RETENTION_RATIO = 0.70    # 有效價格保留率低於 70% 視為欄位解析失效
MAX_SAME_STAMP_DUPES_PER_SITE = 3   # 單站單一郵票同輪抓取重複上限

PBKDF2_ITERATIONS = 100_000
PRICE_FIELDS = (
    "site_a_mint",
    "site_a_cto",
    "site_a_used",
    "site_b_mint",
    "site_b_cto",
    "site_b_used",
)

SB_TO_JT_MAP: Dict[str, Tuple[str, str]] = {
    "SB1": ("T", "T51"),
    "SB2": ("T", "T57"),
    "SB3": ("T", "T58"),
    "SB4": ("T", "T60"),
    "SB5": ("J", "J63"),
    "SB6": ("T", "T73"),
    "SB7": ("T", "T70"),
    "SB8": ("T", "T80"),
    "SB9": ("T", "T88"),
    "SB10": ("T", "T83"),
    "SB11": ("T", "T90"),
    "SB12": ("T", "T102"),
    "SB13": ("T", "T107"),
    "SB14": ("T", "T112"),
    "SB15": ("T", "T124"),
    "SB16": ("T", "T133"),
    "SB17": ("T", "T146"),
    "SB18": ("T", "T159"),
}

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


class UnsafeUrlError(ValueError):
    """URL 不符合主機白名單或安全傳輸政策。"""


class SanityCheckError(RuntimeError):
    """抓取結果未通過資料合理性檢查（觸發 Fail-Closed）。"""


# ==========================================
# URL 驗證與安全 HTTP 回應讀取
# ==========================================

def validate_url(url: str) -> str:
    if not url or not isinstance(url, str):
        raise UnsafeUrlError("空白或無效的 URL")

    cleaned = url.strip()
    parsed = urlparse(cleaned)
    scheme = (parsed.scheme or "").lower()
    hostname = (parsed.hostname or "").lower()

    if not hostname or hostname not in ALLOWED_HOST_POLICIES:
        raise UnsafeUrlError(f"主機不在白名單內: {hostname!r}")
    if scheme not in ALLOWED_HOST_POLICIES[hostname]:
        raise UnsafeUrlError(f"主機 {hostname!r} 不允許使用協定 {scheme!r}")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeUrlError("URL 禁止包含認證資訊")
    if parsed.port not in (None, 80, 443):
        raise UnsafeUrlError(f"不允許的非標準連接埠: {parsed.port!r}")

    return cleaned


def _decode_html_bytes(raw_bytes: bytes, content_type: str) -> str:
    charset = None
    m_ct = re.search(r"charset=([a-zA-Z0-9_\-]+)", content_type, re.IGNORECASE)
    if m_ct:
        charset = m_ct.group(1).strip().lower()
    if not charset:
        m_meta = re.search(br"charset=['\"]?([a-zA-Z0-9_\-]+)", raw_bytes[:4096], re.IGNORECASE)
        if m_meta:
            charset = m_meta.group(1).decode("ascii", errors="ignore").lower()
    if charset in ("gb2312", "gbk"):
        charset = "gb18030"

    candidates = [charset] if charset else []
    for enc in ("utf-8", "gb18030"):
        if enc not in candidates:
            candidates.append(enc)

    for enc in candidates:
        try:
            return raw_bytes.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw_bytes.decode("utf-8", errors="replace")


def _polite_sleep() -> None:
    """強制請求間隔 2 至 3 秒，防止對目標伺服器造成負擔。"""
    time.sleep(random.uniform(2.0, 3.0))


def _fetch_html(session: requests.Session, url: str, referer: str = "") -> Optional[str]:
    """手動驗證每一次轉址與回應標頭／內容上限，不跟隨至非白名單網域。"""
    try:
        current_url = validate_url(url)
    except UnsafeUrlError as exc:
        logger.warning("已拒絕不安全 URL [%s]: %s", url, exc)
        return None

    req_headers = dict(HEADERS)
    if referer:
        try:
            req_headers["Referer"] = validate_url(referer)
        except UnsafeUrlError:
            pass

    for _ in range(MAX_REDIRECTS + 1):
        try:
            resp = session.get(
                current_url,
                headers=req_headers,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=False,
                stream=True,
            )
        except requests.RequestException as exc:
            logger.warning("請求失敗，已安全跳過 [%s]: %s", current_url, type(exc).__name__)
            return None

        try:
            if resp.status_code in (301, 302, 303, 307, 308):
                loc = (resp.headers.get("Location") or "").strip()
                if not loc:
                    logger.warning("轉址缺少 Location 標頭 [%s]", current_url)
                    return None
                next_url = urljoin(current_url, loc)
                try:
                    current_url = validate_url(next_url)
                except UnsafeUrlError as exc:
                    logger.warning("已拒絕不安全轉址目標 [%s -> %s]: %s", current_url, next_url, exc)
                    return None
                continue

            if resp.status_code != 200:
                logger.warning("HTTP 狀態碼異常 [%s]: %d", current_url, resp.status_code)
                return None

            ctype = (resp.headers.get("Content-Type") or "").lower()
            if not any(allowed in ctype for allowed in ALLOWED_CONTENT_TYPES):
                logger.warning("拒絕非 HTML Content-Type [%s]: %r", current_url, ctype)
                return None

            clen = resp.headers.get("Content-Length")
            if clen is not None:
                try:
                    if int(clen) < 0 or int(clen) > MAX_RESPONSE_BYTES:
                        logger.warning("拒絕過大 Content-Length [%s]: %s", current_url, clen)
                        return None
                except ValueError:
                    logger.warning("Content-Length 格式異常 [%s]", current_url)
                    return None

            chunks: List[bytes] = []
            total_bytes = 0
            for chunk in resp.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                total_bytes += len(chunk)
                if total_bytes > MAX_RESPONSE_BYTES:
                    logger.warning("串流回應超過 %d bytes 上限，已中止 [%s]", MAX_RESPONSE_BYTES, current_url)
                    return None
                chunks.append(chunk)

            raw_bytes = b"".join(chunks)
            if not raw_bytes.strip():
                logger.warning("收到空白回應 [%s]", current_url)
                return None

            text = _decode_html_bytes(raw_bytes, ctype)
            if not re.search(r"<(?:!doctype\s+html|html|body|table)\b", text[:8192], re.IGNORECASE):
                logger.warning("回應缺少基本 HTML 結構 [%s]", current_url)
                return None

            return text
        finally:
            resp.close()

    logger.warning("轉址次數超過上限 (%d) [%s]", MAX_REDIRECTS, url)
    return None


# ==========================================
# 加密 / 解密模組與原子寫入
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


def atomic_write_text(target_path: Path, content: str) -> None:
    """以同目錄暫存檔 + fsync + os.replace 原子替換目標檔案，防止寫入中斷毀損。"""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target_path.name}.",
        suffix=".tmp",
        dir=str(target_path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(tmp_path), str(target_path))
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


# ==========================================
# 志號、分類與體裁解析
# ==========================================

def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").strip()


def _cn_wen_to_digit(cn_str: str) -> str:
    if cn_str.isdigit():
        return str(int(cn_str))
    if cn_str == "十":
        return "10"
    if cn_str.startswith("十") and len(cn_str) == 2 and cn_str[1] in CN_NUM_MAP:
        return str(10 + CN_NUM_MAP[cn_str[1]])
    if len(cn_str) == 1 and cn_str in CN_NUM_MAP:
        return str(CN_NUM_MAP[cn_str])
    return cn_str


def _detect_page_category(text: str) -> str:
    cleaned = _nfkc(text)
    if any(k in cleaned for k in ("小型张", "小本票", "普航欠改")):
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


def _normalize_raw_code_and_name(raw_code: str, raw_name: str) -> Tuple[str, str]:
    code = re.sub(r"\s+", "", _nfkc(raw_code))
    name = re.sub(r"\s+", " ", _nfkc(raw_name))

    if not code:
        m = re.match(
            r"^("
            r"文\s*\d+(?:-\d+)?|"
            r"文[一二三四五六七八九十]{1,3}|"
            r"特\s*\d+|"
            r"[纪紀]\s*\d+|"
            r"J\s*\d+M?|"
            r"T\s*\d+M?|"
            r"SB\s*\d+|"
            r"C\s*\d+M?|"
            r"S\s*\d+M?|"
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
        m_wen_cn = re.match(r"^文([一二三四五六七八九十]{1,3})$", code)
        if m_wen_cn:
            code = f"文{_cn_wen_to_digit(m_wen_cn.group(1))}"
        code = re.sub(r"^纪", "紀", code)
        m_alpha = re.match(r"^([a-zA-Z]+)(.*)$", code)
        if m_alpha:
            code = m_alpha.group(1).upper() + m_alpha.group(2)

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


def parse_catalog_and_category(
    raw_code: str,
    raw_name: str,
    page_category: str,
    special_type: str = "",
) -> Tuple[str, str, str, str]:
    code, name = _normalize_raw_code_and_name(raw_code, raw_name)
    row_cat = page_category

    if special_type == "小型张" or "小型张" in name or "小全张" in name:
        m_jt = re.match(r"^([JT]\d+)M?$", code, re.IGNORECASE)
        if m_jt:
            code = m_jt.group(1).upper()
            row_cat = code[0]
        else:
            row_cat = "其他"
        if "小型张" not in name and "小全张" not in name:
            name = f"{name}（小型张）"

    elif special_type == "小本票" or "小本票" in name or re.match(r"^SB\d+$", code, re.IGNORECASE):
        sb_upper = code.upper()
        if sb_upper in SB_TO_JT_MAP:
            jt_cat, jt_code = SB_TO_JT_MAP[sb_upper]
            row_cat = jt_cat
            code = jt_code
            if sb_upper not in name:
                name = f"{name}（{sb_upper} 小本票）" if "小本票" not in name else f"{name}（{sb_upper}）"
            elif "小本票" not in name:
                name = f"{name}（小本票）"
        else:
            row_cat = "其他"
            if "小本票" not in name:
                name = f"{name}（小本票）"

    elif special_type == "普航欠改":
        row_cat = "其他"

    else:
        if row_cat == "其他":
            row_cat = _detect_page_category(f"{code} {name}")

        if row_cat in {"特", "紀", "J", "T"} and code and not re.match(r"^[特紀JT]", code):
            code = f"{row_cat}{code}"
        elif row_cat == "文" and code and re.match(r"^\d+", code):
            code = f"文{code}"

    canon_id = _build_canonical_id(row_cat, code, name)
    return canon_id, row_cat, code, name


def _migrate_existing_store(store: Dict[str, Any]) -> None:
    old_stamps = store.get("stamps", {})
    migrated: Dict[str, Any] = {}

    for _, entry in old_stamps.items():
        raw_cat = entry.get("category", "其他")
        raw_cat = "紀" if raw_cat == "纪" else ("年冊" if raw_cat == "年册" else raw_cat)
        raw_code = entry.get("code", "")
        raw_name = entry.get("name", "")

        special_type = ""
        if "小型张" in raw_name or "小全张" in raw_name:
            special_type = "小型张"
        elif "小本票" in raw_name or re.match(r"^SB\d+$", raw_code, re.IGNORECASE):
            special_type = "小本票"
        elif re.search(r"普/R|欠资|军邮|改值|航空", f"{raw_code} {raw_name}"):
            special_type = "普航欠改"

        canon_id, cat, code, name = parse_catalog_and_category(
            raw_code, raw_name, raw_cat, special_type=special_type
        )

        converted_hist: List[Dict[str, Any]] = []
        for h in entry.get("history", []):
            dt = str(h.get("date", ""))
            if not dt or dt < MIN_VALID_DATE:
                continue
            if "site_a_mint" in h or "site_b_mint" in h:
                point = {"date": dt, "ts": h.get("ts", ""), **{f: h.get(f) for f in PRICE_FIELDS}}
            else:
                point = {
                    "date": dt,
                    "ts": h.get("ts", ""),
                    "site_a_mint": h.get("mint"),
                    "site_a_cto": h.get("cto"),
                    "site_a_used": h.get("used"),
                    "site_b_mint": None,
                    "site_b_cto": None,
                    "site_b_used": None,
                }
            converted_hist.append(point)

        if not converted_hist:
            continue

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
            f"無法解密現有 {DATA_ENC_PATH}（請確認 ENCRYPT_PASSWORD 是否正確）: {type(exc).__name__}"
        ) from exc


# ==========================================
# 日期、價格與 HTML 表格解析
# ==========================================

def _is_valid_calendar_date(y: int, m: int, d: int, today: Optional[datetime] = None) -> Optional[str]:
    now_dt = today or datetime.now()
    try:
        dt = datetime(y, m, d)
    except ValueError:
        return None
    date_str = dt.strftime("%Y-%m-%d")
    max_allowed = (now_dt + timedelta(days=MAX_FUTURE_DAYS)).strftime("%Y-%m-%d")
    if date_str < MIN_VALID_DATE or date_str > max_allowed:
        return None
    return date_str


def _extract_publish_date(soup: BeautifulSoup, title_text: str, today: Optional[datetime] = None) -> str:
    page_text = _nfkc(soup.get_text(" ", strip=True))
    m_pub = re.search(
        r"(?:发布时间|更新时间|发布日期)\s*[：:]\s*(\d{4})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})\s*日?",
        page_text,
    )
    if m_pub:
        valid = _is_valid_calendar_date(int(m_pub.group(1)), int(m_pub.group(2)), int(m_pub.group(3)), today=today)
        if valid:
            return valid

    for meta in soup.find_all("meta"):
        prop = (meta.get("property") or meta.get("name") or "").lower()
        if prop in ("article:published_time", "og:release_date", "pubdate", "publishdate"):
            content = (meta.get("content") or "").strip()
            m_meta = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", content)
            if m_meta:
                valid = _is_valid_calendar_date(
                    int(m_meta.group(1)), int(m_meta.group(2)), int(m_meta.group(3)), today=today
                )
                if valid:
                    return valid

    clean_title = _nfkc(title_text)
    m_title = re.search(r"(\d{4})\s*[年\-/]\s*(\d{1,2})\s*[月\-/]\s*(\d{1,2})\s*日?", clean_title)
    if m_title:
        valid = _is_valid_calendar_date(
            int(m_title.group(1)), int(m_title.group(2)), int(m_title.group(3)), today=today
        )
        if valid:
            return valid

    return (today or datetime.now()).strftime("%Y-%m-%d")


def _parse_price(raw_text: str) -> Optional[float]:
    text = _nfkc(raw_text)
    if not text or all(ch in "-—/\\暂无待定议价电 " for ch in text):
        return None
    if re.match(r"^[+-]?\s*-\s*\d|^-\s*\d", text):
        logger.warning("忽略負數價格字串: %r", text[:32])
        return None

    multiplier = 10000.0 if "万" in text else 1.0
    nums = re.findall(r"\d+(?:\.\d+)?", text)
    if not nums:
        return None

    try:
        values = [float(n) * multiplier for n in nums[:2]]
        price = sum(values) / len(values) if "-" in text and len(values) == 2 else values[0]
        if not math.isfinite(price) or price < 0 or price > MAX_PRICE_YUAN:
            logger.warning("忽略超出合理範圍之價格: %r", text[:32])
            return None
        return round(price, 2)
    except ValueError:
        return None


def _classify_header(col_text: str) -> str:
    h = re.sub(r"\s+", "", _nfkc(col_text))
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
    special_type: str = "",
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
            if not clean_check or clean_check in ("名称", "邮票名称", "编号", "品种", "名称(老本)", "名称(新本)"):
                continue
            if mint_price is None and cto_price is None and used_price is None:
                continue

            canon_id, row_cat, code, name = parse_catalog_and_category(
                raw_code, raw_name, page_category, special_type=special_type
            )
            if not name:
                continue

            results.append(
                {
                    "site": site_key,
                    "id": canon_id,
                    "code": code,
                    "name": name,
                    "category": row_cat,
                    "mint": mint_price,
                    "cto": cto_price,
                    "used": used_price,
                    "date": record_date,
                }
            )
            if len(results) >= MAX_RECORDS_PER_PAGE:
                return results

    return results


# ==========================================
# 資料源 A 與 B 爬蟲
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
    seen_urls = set()
    articles: List[Tuple[str, str]] = []

    for idx, list_url in enumerate(CNGOLD_URLS):
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
                try:
                    validate_url(full_url)
                except UnsafeUrlError:
                    continue
                parsed = urlparse(full_url)
                if not re.search(r"/cang/[a-z]+\d+\.html$", parsed.path):
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
            logger.error("[Site A] 解析列表失敗 [%s]: %s", list_url, type(exc).__name__)

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
            category = _detect_page_category(combined)
            record_date = _extract_publish_date(soup, combined)

            page_rows: List[Dict[str, Any]] = []
            for tbl in soup.find_all("table"):
                page_rows.extend(_parse_table(tbl, "site_a", category, record_date))

            logger.info("[Site A] %s -> 分類:[%s] 更新日:[%s] %d 筆", article_title[:24], category, record_date, len(page_rows))
            records.extend(page_rows)
            processed += 1
        except Exception as exc:
            logger.warning("[Site A] 解析異常已跳過 [%s]: %s", url, type(exc).__name__)

    return records, processed


def scrape_site_b(session: requests.Session, max_pages: int = 20) -> Tuple[List[Dict[str, Any]], int]:
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
            try:
                validate_url(full_url)
            except UnsafeUrlError:
                continue
            parsed = urlparse(full_url)
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
        logger.error("[Site B] 解析首頁失敗: %s", type(exc).__name__)
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
            title_combo = f"{page_title} {link_title}"

            special_type = ""
            if "小型张" in title_combo:
                special_type = "小型张"
            elif "小本票" in title_combo:
                special_type = "小本票"
            elif "普航欠改" in title_combo:
                special_type = "普航欠改"

            category = "其他" if special_type else _detect_page_category(title_combo)
            record_date = _extract_publish_date(soup, title_combo)

            page_rows: List[Dict[str, Any]] = []
            for tbl in soup.find_all("table"):
                page_rows.extend(
                    _parse_table(
                        tbl,
                        site_key="site_b",
                        page_category=category,
                        record_date=record_date,
                        special_type=special_type,
                    )
                )

            logger.info("[Site B] %s -> 分類:[%s] 更新日:[%s] %d 筆", page_title[:24], category, record_date, len(page_rows))
            records.extend(page_rows)
            processed += 1
        except Exception as exc:
            logger.warning("[Site B] 解析異常已跳過 [%s]: %s", url, type(exc).__name__)

    return records, processed


# ==========================================
# Fail-Closed 資料合理性檢查（所有來源一體適用）與寫入
# ==========================================

def run_sanity_checks(
    existing_store: Dict[str, Any],
    all_records: List[Dict[str, Any]],
    today: Optional[datetime] = None,
) -> None:
    if not all_records:
        raise SanityCheckError("未抓取到任何有效記錄")
    if len(all_records) > MAX_RECORDS_PER_RUN:
        raise SanityCheckError(f"抓取總筆數異常過大 ({len(all_records)} > {MAX_RECORDS_PER_RUN})")

    now_dt = today or datetime.now()
    max_allowed_date = (now_dt + timedelta(days=MAX_FUTURE_DAYS)).strftime("%Y-%m-%d")

    dupes: Dict[Tuple[str, str], int] = {}
    scraped_by_site: Dict[str, List[Dict[str, Any]]] = {site: [] for site in ACTIVE_SITES}

    for rec in all_records:
        dt = str(rec.get("date", ""))
        try:
            datetime.strptime(dt, "%Y-%m-%d")
        except ValueError as exc:
            raise SanityCheckError(f"無效日期格式: {dt!r}") from exc

        if dt < MIN_VALID_DATE or dt > max_allowed_date:
            raise SanityCheckError(f"日期超出合理範圍: {dt!r}（容許 {MIN_VALID_DATE} ~ {max_allowed_date}）")

        for cond in ("mint", "cto", "used"):
            p = rec.get(cond)
            if p is not None and (
                not isinstance(p, (int, float)) or not math.isfinite(p) or p < 0 or p > MAX_PRICE_YUAN
            ):
                raise SanityCheckError(f"發現異常價格數值 [{rec.get('id')}]: {p!r}")

        site = rec["site"]
        scraped_by_site.setdefault(site, []).append(rec)
        dupe_key = (site, rec["id"])
        dupes[dupe_key] = dupes.get(dupe_key, 0) + 1
        if dupes[dupe_key] > MAX_SAME_STAMP_DUPES_PER_SITE:
            raise SanityCheckError(
                f"單站單一郵票重複過多次 [{site} / {rec['id']}]: {dupes[dupe_key]} 次"
            )

    existing_stamps = existing_store.get("stamps", {})

    # 對所有來源逐一檢查：日期倒退、來源中斷、品種覆蓋率、品相欄位保留率
    for site in ACTIVE_SITES:
        site_recs = scraped_by_site.get(site, [])
        site_ids = {r["id"] for r in site_recs}

        prev_site_ids = {
            sid
            for sid, entry in existing_stamps.items()
            if entry.get("history")
            and any(entry["history"][-1].get(f"{site}_{c}") is not None for c in ("mint", "cto", "used"))
        }

        if len(prev_site_ids) >= MIN_STAMPS_FOR_COUNT_CHECK:
            if not site_ids:
                raise SanityCheckError(f"來源 {site} 本次完全無資料（既有 {len(prev_site_ids)} 款）")
            site_drop_ratio = 1.0 - (len(prev_site_ids & site_ids) / len(prev_site_ids))
            if site_drop_ratio > MAX_STAMP_DROP_RATIO:
                raise SanityCheckError(
                    f"來源 {site} 覆蓋率大幅下降（減少 {site_drop_ratio:.1%}，既有 {len(prev_site_ids)}，本次命中 {len(prev_site_ids & site_ids)}）"
                )

        if site_recs:
            prev_dates = [
                entry["history"][-1]["date"]
                for entry in existing_stamps.values()
                if entry.get("history")
                and any(entry["history"][-1].get(f"{site}_{c}") is not None for c in ("mint", "cto", "used"))
            ]
            if prev_dates:
                prev_max = max(prev_dates)
                new_max = max(r["date"] for r in site_recs)
                if new_max < prev_max:
                    raise SanityCheckError(f"{site} 更新日期倒退（本次最新 {new_max} < 既有最新 {prev_max}）")

        for cond in ("mint", "cto", "used"):
            field = f"{site}_{cond}"
            prev_field_ids = {
                sid
                for sid, entry in existing_stamps.items()
                if entry.get("history") and entry["history"][-1].get(field) is not None
            }
            overlap = prev_field_ids & site_ids
            if len(overlap) >= MIN_FIELD_COUNT_FOR_RETENTION:
                retained_ids = {r["id"] for r in site_recs if r["id"] in overlap and r.get(cond) is not None}
                retention_ratio = len(retained_ids) / len(overlap)
                if retention_ratio < MIN_PRICE_RETENTION_RATIO:
                    raise SanityCheckError(
                        f"欄位 {field} 有效價格大量消失（保留率 {retention_ratio:.1%} < {MIN_PRICE_RETENTION_RATIO:.0%}）"
                    )

    if len(existing_stamps) < MIN_STAMPS_FOR_COUNT_CHECK:
        return

    existing_ids = set(existing_stamps.keys())
    scraped_ids = {r["id"] for r in all_records}
    drop_ratio = 1.0 - (len(existing_ids & scraped_ids) / len(existing_ids))
    if drop_ratio > MAX_STAMP_DROP_RATIO:
        raise SanityCheckError(
            f"整體既有品種覆蓋率大幅下降（減少 {drop_ratio:.1%}，既有 {len(existing_ids)} 款，本次命中 {len(existing_ids & scraped_ids)} 款）"
        )

    growth_ratio = len(scraped_ids - existing_ids) / len(existing_ids)
    if growth_ratio > MAX_STAMP_GROWTH_RATIO:
        raise SanityCheckError(
            f"新增品種異常暴增（+{growth_ratio:.1%}，新增 {len(scraped_ids - existing_ids)} 款）"
        )


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
            for site in ACTIVE_SITES:
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

    # Fail-closed 檢查：任一來源結構異常即中止，絕不寫入／覆寫 docs/data.enc
    run_sanity_checks(store, all_records)

    appended, merged_count = apply_dual_source_smart_diff(store, all_records, checked_at)
    encrypted_output = encrypt_json_payload(store, password)
    atomic_write_text(DATA_ENC_PATH, encrypted_output)

    logger.info(
        "去重合併完成！共 %d 款獨立郵票，新增/更新 %d 筆價格變動紀錄，已原子寫入 %s",
        merged_count,
        appended,
        DATA_ENC_PATH,
    )


if __name__ == "__main__":
    main()