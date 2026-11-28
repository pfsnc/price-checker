import argparse
import json
import logging
import os
from pathlib import Path

from scraper import (
    DATA_ENC_PATH,
    _migrate_existing_store,
    decrypt_json_payload,
    save_store_to_cf,
    validate_cf_url,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("migrate_to_cf")


def main() -> None:
    parser = argparse.ArgumentParser(description="解密 docs/data.enc 並匯出／遷移至 Cloudflare D1")
    parser.add_argument(
        "--export-json",
        default="",
        help="選填：於本機匯出解密後的 JSON 檔案路徑（請勿提交至公開 Git Repo）",
    )
    parser.add_argument(
        "--skip-upload",
        action="store_true",
        help="僅解密匯出，不上傳至 Cloudflare D1",
    )
    args = parser.parse_args()

    password = os.environ.get("ENCRYPT_PASSWORD", "").strip()
    if not password:
        raise RuntimeError("環境變數 ENCRYPT_PASSWORD 未設定，無法解密 docs/data.enc。")

    if not DATA_ENC_PATH.exists():
        raise FileNotFoundError(f"找不到 {DATA_ENC_PATH}，無須遷移。")

    raw_enc = DATA_ENC_PATH.read_text(encoding="utf-8")
    store = decrypt_json_payload(raw_enc, password)
    _migrate_existing_store(store)

    stamp_count = len(store.get("stamps", {}))
    history_count = sum(len(s.get("history", [])) for s in store.get("stamps", {}).values())
    logger.info("已成功解密 docs/data.enc：共 %d 款郵票、%d 筆歷史紀錄。", stamp_count, history_count)

    if args.export_json:
        out_path = Path(args.export_json).resolve()
        out_path.write_text(
            json.dumps(store, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("已匯出解密後 JSON 至 %s（請妥善保管，切勿提交到公開 GitHub）。", out_path)

    if not args.skip_upload:
        cf_url = validate_cf_url(os.environ.get("CF_WORKER_URL", ""))
        ingest_token = os.environ.get("CF_INGEST_TOKEN", "").strip()
        if not ingest_token:
            raise RuntimeError("環境變數 CF_INGEST_TOKEN 未設定。")

        chunks_written = save_store_to_cf(cf_url, ingest_token, store, password)
        logger.info("已將全部加密資料分塊上傳至 Cloudflare D1（共 %d 個分塊）。", chunks_written)


if __name__ == "__main__":
    main()
