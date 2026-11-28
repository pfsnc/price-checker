import base64
import json
import logging
import math
import os
import random
import re
import time
import unicodedata
from dataclasses import dataclass
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
# 集中管理：來源白名單、傳輸限制與格式檢查參數
# ==========================================

# 主機白名單僅能防範 SSRF 與惡意轉址，無法防範網域過期搶註或 HTTP 明文遭中間人竄改。
# 點購網目前僅提供 HTTP，其回應內容一律視為不可信輸入，必須通過回應格式與結構驗證。
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
CF_REQUEST_TIMEOUT = 30
MAX_REDIRECTS = 5
MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 單頁回應上限 5 MB
ALLOWED_CONTENT_TYPES = ("text/html", "application/xhtml+xml")

MIN_VALID_DATE = "2020-01-01"
MAX_FUTURE_DAYS = 1
MAX_PRICE_YUAN = 50_000_000.0
MAX_RECORDS_PER_PAGE = 1_000
MAX_RECORDS_PER_RUN = 5_000
HISTORY_RETENTION_DAYS = 365
MAX_CHUNK_PLAIN_BYTES = 800_000  # 控制每塊密文遠低於 D1 單列 2 MB 上限

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


class FormatCompatibilityError(RuntimeError):
    """確認來源網頁格式異常或已無法預期解析。"""


@dataclass
class FetchResult:
    html: Optional[str]
    status: str  # "ok" | "network_error" | "security_error" | "format_error"
    reason: str = ""


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


def validate_cf_url(url: str) -> str:
    if not url or not isinstance(url, str):
        raise UnsafeUrlError("CF_WORKER_URL 未設定")
    cleaned = url.strip()
    parsed = urlparse(cleaned)
    if (parsed.scheme or "").lower() != "https":
        raise UnsafeUrlError("CF_WORKER_URL 必須使用 https")
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise UnsafeUrlError("CF_WORKER_URL 格式不合法")
    if parsed.port not in (None, 443):
        raise UnsafeUrlError("CF_WORKER_URL 禁止使用非標準連接埠")
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
    time.sleep(random.uniform(2.0, 3.0))


def _fetch_html(session: requests.Session, url: str, referer: str = "") -> FetchResult:
    try:
        current_url = validate_url(url)
    except UnsafeUrlError as exc:
        logger.warning("[安全檢查] 已拒絕不安全 URL [%s]: %s", url, exc)
        return FetchResult(None, "security_error", str(exc))

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
            logger.warning("[網路暫時錯誤] 連線失敗或超時，已安全跳過 [%s]: %s", current_url, type(exc).__name__)
            return FetchResult(None, "network_error", type(exc).__name__)

        try:
            if resp.status_code in (301, 302, 303, 307, 308):
                loc = (resp.headers.get("Location") or "").strip()
                if not loc:
                    return FetchResult(None, "format_error", "redirect_missing_location")
                next_url = urljoin(current_url, loc)
                try:
                    current_url = validate_url(next_url)
                except UnsafeUrlError as exc:
                    logger.warning("[安全檢查] 已拒絕不安全轉址目標 [%s -> %s]: %s", current_url, next_url, exc)
                    return FetchResult(None, "security_error", str(exc))
                continue

            if resp.status_code >= 500 or resp.status_code in (408, 429):
                logger.warning("[網路暫時錯誤] 伺服器暫時回應 HTTP %d [%s]", resp.status_code, current_url)
                return FetchResult(None, "network_error", f"http_{resp.status_code}")

            if resp.status_code != 200:
                logger.warning("[HTTP 錯誤] 非 200 狀態碼 HTTP %d [%s]", resp.status_code, current_url)
                return FetchResult(None, "network_error", f"http_{resp.status_code}")

            ctype = (resp.headers.get("Content-Type") or "").lower()
            if not any(allowed in ctype for allowed in ALLOWED_CONTENT_TYPES):
                logger.error("[格式異常] 拒絕非 HTML Content-Type [%s]: %r", current_url, ctype)
                return FetchResult(None, "format_error", "non_html_content_type")

            clen = resp.headers.get("Content-Length")
            if clen is not None:
                try:
                    if int(clen) < 0 or int(clen) > MAX_RESPONSE_BYTES:
                        logger.error("[格式異常] 回應 Content-Length 超出上限 [%s]", current_url)
                        return FetchResult(None, "format_error", "content_length_exceeded")
                except ValueError:
                    return FetchResult(None, "format_error", "invalid_content_length")

            chunks: List[bytes] = []
            total_bytes = 0
            for chunk in resp.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                total_bytes += len(chunk)
                if total_bytes > MAX_RESPONSE_BYTES:
                    logger.error("[格式異常] 串流回應超過 %d bytes 上限 [%s]", MAX_RESPONSE_BYTES, current_url)
                    return FetchResult(None, "format_error", "response_too_large")
                chunks.append(chunk)

            raw_bytes = b"".join(chunks)
            if not raw_bytes.strip():
                logger.error("[格式異常] 收到空白 HTTP 200 回應 [%s]", current_url)
                return FetchResult(None, "format_error", "empty_body")

            text = _decode_html_bytes(raw_bytes, ctype)
            if not re.search(r"<(?:!doctype\s+html|html|body|table)\b", text[:8192], re.IGNORECASE):
                logger.error("[格式異常] 回應缺少基本 HTML 結構 [%s]", current_url)
                return FetchResult(None, "format_error", "missing_html_structure")

            return FetchResult(text, "ok")
        finally:
            resp.close()

    return FetchResult(None, "format_error", "too_many_redirects")


# ==========================================
# 加密 / 解密與 Cloudflare D1 分塊讀寫
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


def split_store_into_chunks(
    store: Dict[str, Any],
    max_plain_bytes: int = MAX_CHUNK_PLAIN_BYTES,
) -> Dict[str, Dict[str, Any]]:
    """依分類將資料分塊，確保每列密文尺寸遠低於 Cloudflare D1 的 2 MB 單列上限。"""
    chunks: Dict[str, Dict[str, Any]] = {
        "__meta__": {
            "updated_at": store.get("updated_at", ""),
            "last_checked": store.get("last_checked", ""),
        }
    }
    by_cat: Dict[str, Dict[str, Any]] = {}
    for cid, entry in store.get("stamps", {}).items():
        cat = entry.get("category") or "其他"
        by_cat.setdefault(cat, {})[cid] = entry

    for cat, stamps_dict in by_cat.items():
        raw = json.dumps({"stamps": stamps_dict}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(raw) <= max_plain_bytes:
            chunks[cat] = {"stamps": stamps_dict}
            continue

        part_idx = 0
        cur_dict: Dict[str, Any] = {}
        cur_size = 16
        for cid, entry in stamps_dict.items():
            entry_bytes = len(json.dumps({cid: entry}, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            if cur_dict and cur_size + entry_bytes > max_plain_bytes:
                chunks[f"{cat}#{part_idx}"] = {"stamps": cur_dict}
                part_idx += 1
                cur_dict = {}
                cur_size = 16
            cur_dict[cid] = entry
            cur_size += entry_bytes
        if cur_dict:
            chunks[f"{cat}#{part_idx}"] = {"stamps": cur_dict}

    return chunks


def load_existing_store_from_cf(cf_url: str, password: str) -> Dict[str, Any]:
    """從 Cloudflare Worker 讀取並解密現有資料；若遠端連線失敗採 fail-closed 避免覆蓋舊史。"""
    try:
        resp = requests.get(cf_url, timeout=CF_REQUEST_TIMEOUT, allow_redirects=False)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        raise RuntimeError(f"無法從 Cloudflare 讀取現有資料庫（已中止避免覆寫）: {type(exc).__name__}") from exc

    if isinstance(payload, dict) and isinstance(payload.get("chunks"), list):
        chunks = payload["chunks"]
        if not chunks:
            logger.info("Cloudflare D1 尚無資料，將初始化全新資料集。")
            return {"updated_at": "", "last_checked": "", "stamps": {}}

        store: Dict[str, Any] = {"updated_at": "", "last_checked": "", "stamps": {}}
        try:
            for item in chunks:
                key = str(item.get("chunk_key") or "")
                obj = decrypt_json_payload(item["envelope_json"], password)
                if key == "__meta__":
                    store["updated_at"] = obj.get("updated_at", "")
                    store["last_checked"] = obj.get("last_checked", "")
                elif isinstance(obj.get("stamps"), dict):
                    store["stamps"].update(obj["stamps"])
        except Exception as exc:
            raise RuntimeError(
                f"無法解密 Cloudflare D1 中的資料（請確認 ENCRYPT_PASSWORD 是否一致）: {type(exc).__name__}"
            ) from exc

        _migrate_existing_store(store)
        return store

    raise RuntimeError("Cloudflare Worker 回傳格式非預期（缺少 chunks 陣列）。")


def save_store_to_cf(cf_url: str, ingest_token: str, store: Dict[str, Any], password: str) -> int:
    """分塊 AES-256-GCM 加密後透過原子 batch API 寫入 Cloudflare D1。"""
    plain_chunks = split_store_into_chunks(store)
    updated_at = str(store.get("last_checked") or "")
    out_chunks = [
        {
            "chunk_key": key,
            "envelope_json": encrypt_json_payload(obj, password),
            "updated_at": updated_at,
        }
        for key, obj in plain_chunks.items()
    ]

    resp = requests.post(
        cf_url,
        json={"chunks": out_chunks},
        headers={
            "Authorization": f"Bearer {ingest_token}",
            "Content-Type": "application/json",
        },
        timeout=CF_REQUEST_TIMEOUT,
        allow_redirects=False,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"寫入 Cloudflare D1 失敗（HTTP {resp.status_code}）")

    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError("Cloudflare D1 回應寫入未成功")
    return len(out_chunks)


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


def scrape_site_a(
    session: requests.Session,
    per_source_limit: int = 10,
) -> Tuple[List[Dict[str, Any]], int, List[str]]:
    seen_urls = set()
    articles: List[Tuple[str, str]] = []
    format_errors: List[str] = []
    list_pages_ok = 0

    for idx, list_url in enumerate(CNGOLD_URLS):
        if idx > 0:
            _polite_sleep()
        res = _fetch_html(session, list_url, referer="https://m.cngold.org/cang/")
        if res.status == "network_error":
            continue
        if res.status in ("format_error", "security_error"):
            format_errors.append(f"[Site A] 列表頁異常 ({list_url}): {res.status} / {res.reason}")
            continue

        list_pages_ok += 1
        html = res.html or ""
        source_count = 0
        try:
            soup = BeautifulSoup(html, "html.parser")
            if "邮票" not in html and "金投" not in html:
                format_errors.append(f"[Site A] 列表頁內容與預期網站不符 ({list_url})")
                continue

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

            if source_count == 0:
                format_errors.append(f"[Site A] 列表頁可連線但找不到預期行情文章連結 ({list_url})")
        except Exception as exc:
            format_errors.append(f"[Site A] 列表頁 HTML 解析失敗 ({list_url}): {type(exc).__name__}")

    records: List[Dict[str, Any]] = []
    articles_ok = 0
    for url, list_title in articles:
        _polite_sleep()
        res = _fetch_html(session, url, referer="https://m.cngold.org/cang/")
        if res.status != "ok" or not res.html:
            continue

        try:
            soup = BeautifulSoup(res.html, "html.parser")
            article_title = _extract_cngold_title(soup, list_title)
            combined = f"{article_title} {list_title}"
            category = _detect_page_category(combined)
            record_date = _extract_publish_date(soup, combined)

            page_rows: List[Dict[str, Any]] = []
            for tbl in soup.find_all("table"):
                page_rows.extend(_parse_table(tbl, "site_a", category, record_date))

            logger.info("[Site A] %s -> 分類:[%s] 更新日:[%s] %d 筆", article_title[:24], category, record_date, len(page_rows))
            records.extend(page_rows)
            articles_ok += 1
        except Exception as exc:
            logger.warning("[Site A] 單篇文章解析跳過 [%s]: %s", url, type(exc).__name__)

    if list_pages_ok > 0 and articles_ok > 0 and not records:
        format_errors.append("[Site A] 成功取得文章 HTML 但表格結構無法解析出任何有效報價")

    return records, articles_ok, format_errors


def scrape_site_b(
    session: requests.Session,
    max_pages: int = 20,
) -> Tuple[List[Dict[str, Any]], int, List[str]]:
    format_errors: List[str] = []
    index_res = _fetch_html(session, SC5151_INDEX_URL, referer="http://www.5151sc.com/")
    if index_res.status == "network_error":
        return [], 0, format_errors
    if index_res.status in ("format_error", "security_error") or not index_res.html:
        format_errors.append(f"[Site B] 首頁回應異常 ({SC5151_INDEX_URL}): {index_res.status} / {index_res.reason}")
        return [], 0, format_errors

    index_html = index_res.html
    if "邮票" not in index_html:
        format_errors.append("[Site B] 首頁 HTML 內容缺少預期郵票網站標記，可能網域異動或改版")
        return [], 0, format_errors

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
        format_errors.append(f"[Site B] 首頁解析失敗: {type(exc).__name__}")
        return [], 0, format_errors

    if not subpages:
        format_errors.append("[Site B] 首頁可連線但找不到任何預期子頁面連結")
        return [], 0, format_errors

    records: List[Dict[str, Any]] = []
    pages_ok = 0
    for url, link_title in subpages:
        _polite_sleep()
        res = _fetch_html(session, url, referer=SC5151_INDEX_URL)
        if res.status != "ok" or not res.html:
            continue

        try:
            soup = BeautifulSoup(res.html, "html.parser")
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
            pages_ok += 1
        except Exception as exc:
            logger.warning("[Site B] 單一子頁解析跳過 [%s]: %s", url, type(exc).__name__)

    if pages_ok > 0 and not records:
        format_errors.append("[Site B] 成功取得子頁 HTML 但表格結構無法解析出任何有效報價")

    return records, pages_ok, format_errors


# ==========================================
# 基本欄位格式檢查、合併與 365 天歷史清理
# ==========================================

def run_sanity_checks(
    all_records: List[Dict[str, Any]],
    today: Optional[datetime] = None,
) -> None:
    if len(all_records) > MAX_RECORDS_PER_RUN:
        raise FormatCompatibilityError(
            f"抓取總筆數異常過大 ({len(all_records)} > {MAX_RECORDS_PER_RUN})，疑似頁面結構錯亂"
        )

    now_dt = today or datetime.now()
    max_allowed_date = (now_dt + timedelta(days=MAX_FUTURE_DAYS)).strftime("%Y-%m-%d")

    for rec in all_records:
        if not isinstance(rec.get("id"), str) or not rec["id"]:
            raise FormatCompatibilityError("資料格式錯誤：缺少郵票識別碼")
        if not isinstance(rec.get("name"), str) or not rec["name"]:
            raise FormatCompatibilityError("資料格式錯誤：缺少郵票名稱")

        dt = str(rec.get("date", ""))
        try:
            datetime.strptime(dt, "%Y-%m-%d")
        except ValueError as exc:
            raise FormatCompatibilityError(f"無效日期格式: {dt!r}") from exc

        if dt < MIN_VALID_DATE or dt > max_allowed_date:
            raise FormatCompatibilityError(
                f"日期超出合理範圍: {dt!r}（容許 {MIN_VALID_DATE} ~ {max_allowed_date}）"
            )

        for cond in ("mint", "cto", "used"):
            p = rec.get(cond)
            if p is not None and (
                not isinstance(p, (int, float)) or not math.isfinite(p) or p < 0 or p > MAX_PRICE_YUAN
            ):
                raise FormatCompatibilityError(f"發現異常價格數值 [{rec.get('id')}]: {p!r}")


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


def prune_old_history(
    store: Dict[str, Any],
    reference_dt: datetime,
    retention_days: int = HISTORY_RETENTION_DAYS,
) -> int:
    cutoff_date = reference_dt.date() - timedelta(days=retention_days)
    stamps_map = store.get("stamps", {})
    pruned_count = 0

    for entry in stamps_map.values():
        history = entry.get("history", [])
        if not history:
            continue

        kept: List[Dict[str, Any]] = []
        for point in history:
            dt_str = str(point.get("date") or "")
            try:
                pt_date = datetime.strptime(dt_str, "%Y-%m-%d").date()
            except ValueError:
                pruned_count += 1
                continue

            if pt_date >= cutoff_date:
                kept.append(point)
            else:
                pruned_count += 1

        if not kept:
            latest_point = max(history, key=lambda p: (str(p.get("date") or ""), str(p.get("ts") or "")))
            kept = [latest_point]
            pruned_count = max(0, pruned_count - 1)

        entry["history"] = kept

    return pruned_count


def main() -> None:
    password = os.environ.get("ENCRYPT_PASSWORD", "").strip()
    cf_url = validate_cf_url(os.environ.get("CF_WORKER_URL", ""))
    ingest_token = os.environ.get("CF_INGEST_TOKEN", "").strip()
    if not password or not ingest_token:
        raise RuntimeError("環境變數 ENCRYPT_PASSWORD 或 CF_INGEST_TOKEN 未設定。")

    store = load_existing_store_from_cf(cf_url, password)
    checked_dt = datetime.now()
    checked_at = checked_dt.strftime("%Y-%m-%d %H:%M:%S")

    with requests.Session() as session:
        rec_a, proc_a, err_a = scrape_site_a(session, per_source_limit=10)
        _polite_sleep()
        rec_b, proc_b, err_b = scrape_site_b(session, max_pages=20)

    all_records = rec_a + rec_b
    format_errors = err_a + err_b
    logger.info(
        "雙站擷取摘要 -> Site A: %d 頁 / %d 筆；Site B: %d 頁 / %d 筆",
        proc_a, len(rec_a), proc_b, len(rec_b),
    )

    if format_errors:
        for msg in format_errors:
            logger.error("格式不相容錯誤: %s", msg)
        raise FormatCompatibilityError("偵測到來源網站格式錯誤或已無法預期解析，已中止並保留 Cloudflare D1 舊資料。")

    if not all_records:
        logger.warning("本次因暫時性網路錯誤未取得任何有效報價，保留 Cloudflare D1 舊資料不覆寫。")
        return

    run_sanity_checks(all_records, today=checked_dt)

    appended, merged_count = apply_dual_source_smart_diff(store, all_records, checked_at)
    pruned_count = prune_old_history(store, checked_dt, retention_days=HISTORY_RETENTION_DAYS)
    logger.info("歷史紀錄清理完成（保留最近 %d 天）：已清除 %d 筆舊紀錄。", HISTORY_RETENTION_DAYS, pruned_count)

    chunks_written = save_store_to_cf(cf_url, ingest_token, store, password)
    logger.info(
        "處理完成！檢查 %d 款郵票，新增/更新 %d 筆價格變動，已寫入 %d 個 D1 分塊（last_checked=%s）。",
        merged_count,
        appended,
        chunks_written,
        store["last_checked"],
    )


if __name__ == "__main__":
    main()
