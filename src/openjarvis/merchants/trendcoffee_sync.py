"""TREND Coffee catalog discovery and snapshot synchronization module."""

from __future__ import annotations

import datetime
import json
import logging
from typing import Any, Dict, List, TYPE_CHECKING
import httpx

from openjarvis.tools.file_write import FileWriteTool

if TYPE_CHECKING:
    from openjarvis.scheduler.scheduler import ScheduledTask, TaskScheduler

LOGGER = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://trendcoffee.net/api/latest"
DEFAULT_SNAPSHOT_PATH = "artifacts/trend-coffee/menu_snapshot.json"


def _extract_size_str(size_val: Any) -> str:
    if isinstance(size_val, dict):
        return str(size_val.get("name", "tiêu chuẩn"))
    return str(size_val or "tiêu chuẩn")


def fetch_and_normalize_catalog(
    api_base: str = DEFAULT_API_BASE,
    page_size: int = 50,
    timeout_seconds: float = 10.0,
) -> Dict[str, Any]:
    """Fetch product catalog from TREND Coffee API and normalize into standard schema."""
    url = f"{api_base.rstrip('/')}/products?page=1&pageSize={page_size}"
    resp = httpx.get(url, timeout=timeout_seconds)
    resp.raise_for_status()
    payload = resp.json()

    raw_items = payload.get("result", {}).get("items", [])
    if not isinstance(raw_items, list):
        raw_items = []

    normalized_items: List[Dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue

        category_name = ""
        cat_val = item.get("category")
        if isinstance(cat_val, dict):
            category_name = str(cat_val.get("name", ""))
        elif isinstance(cat_val, str):
            category_name = cat_val

        variants_list: List[Dict[str, Any]] = []
        for v in item.get("variants", []):
            if not isinstance(v, dict):
                continue
            variants_list.append(
                {
                    "variant_slug": str(v.get("slug", "")),
                    "size": _extract_size_str(v.get("size")),
                    "price": int(v.get("price", 0)),
                }
            )

        normalized_items.append(
            {
                "product_slug": str(item.get("slug", "")),
                "name": str(item.get("name", "")),
                "category": category_name,
                "variants": variants_list,
            }
        )

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return {
        "synced_at": now_iso,
        "source": url,
        "branch_slug": "ba9355f797",
        "total_products": len(normalized_items),
        "items": normalized_items,
    }


def sync_trend_coffee_catalog(
    api_base: str = DEFAULT_API_BASE,
    snapshot_path: str = DEFAULT_SNAPSHOT_PATH,
    page_size: int = 50,
    timeout_seconds: float = 10.0,
) -> Dict[str, Any]:
    """Synchronize live catalog and safely update local snapshot file."""
    try:
        catalog = fetch_and_normalize_catalog(
            api_base=api_base,
            page_size=page_size,
            timeout_seconds=timeout_seconds,
        )
        json_content = json.dumps(catalog, ensure_ascii=False, indent=2)

        tool = FileWriteTool()
        result = tool.execute(
            path=snapshot_path,
            content=json_content,
            mode="write",
            create_dirs=True,
        )

        if result.success:
            LOGGER.info(
                "Successfully synchronized TREND Coffee catalog (%d items) to %s",
                catalog["total_products"],
                snapshot_path,
            )
            return {
                "success": True,
                "total_products": catalog["total_products"],
                "snapshot_path": snapshot_path,
                "synced_at": catalog["synced_at"],
            }
        else:
            LOGGER.error("Failed to write snapshot file: %s", result.content)
            return {"success": False, "error": result.content}

    except Exception as exc:
        LOGGER.warning("Catalog synchronization failed, retaining existing snapshot: %s", exc)
        return {"success": False, "error": str(exc)}


def register_trend_coffee_sync_job(
    scheduler: TaskScheduler,
    interval_seconds: int = 3600,
    agent: str = "orchestrator",
) -> ScheduledTask:
    """Register the recurring TREND Coffee catalog synchronization task with the scheduler."""
    prompt = (
        "Dùng http_request lấy danh mục mới nhất từ https://trendcoffee.net/api/latest/products "
        "và dùng file_write cập nhật vào artifacts/trend-coffee/menu_snapshot.json"
    )
    return scheduler.create_task(
        prompt=prompt,
        schedule_type="interval",
        schedule_value=str(interval_seconds),
        agent=agent,
        tools="http_request,file_write",
        metadata={"merchant": "trendcoffee", "job": "catalog_sync"},
    )
