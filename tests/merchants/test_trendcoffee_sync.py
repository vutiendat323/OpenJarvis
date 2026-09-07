import json
from unittest.mock import MagicMock, patch
import httpx
import pytest

from openjarvis.merchants.trendcoffee_sync import (
    sync_trend_coffee_catalog,
    fetch_and_normalize_catalog,
    register_trend_coffee_sync_job,
)
from openjarvis.scheduler.scheduler import TaskScheduler
from openjarvis.scheduler.store import SchedulerStore


def sample_api_response():
    return {
        "statusCode": 200,
        "result": {
            "items": [
                {
                    "slug": "23f99adf51",
                    "name": "Cà phê đen",
                    "category": {"name": "Cà phê"},
                    "variants": [
                        {
                            "slug": "d5de540d4c",
                            "size": {"name": "tiêu chuẩn"},
                            "price": 35000,
                        }
                    ],
                },
                {
                    "slug": "796e4ead93",
                    "name": "Bánh Tiramisu",
                    "category": "Bánh ngọt",
                    "variants": [
                        {
                            "slug": "613bacd3b8",
                            "size": "tiêu chuẩn",
                            "price": 39000,
                        }
                    ],
                },
            ]
        },
    }


def test_fetch_and_normalize_catalog():
    with patch("httpx.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = sample_api_response()
        mock_get.return_value = mock_response

        catalog = fetch_and_normalize_catalog("https://trendcoffee.net/api/latest")
        assert catalog["total_products"] == 2
        assert catalog["items"][0]["name"] == "Cà phê đen"
        assert catalog["items"][0]["product_slug"] == "23f99adf51"
        assert catalog["items"][0]["variants"][0]["variant_slug"] == "d5de540d4c"
        assert catalog["items"][0]["variants"][0]["price"] == 35000
        assert catalog["items"][1]["name"] == "Bánh Tiramisu"
        assert catalog["items"][1]["variants"][0]["variant_slug"] == "613bacd3b8"


def test_sync_trend_coffee_catalog_success(tmp_path):
    target_file = tmp_path / "menu_snapshot.json"
    with patch("httpx.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = sample_api_response()
        mock_get.return_value = mock_response

        result = sync_trend_coffee_catalog(
            api_base="https://trendcoffee.net/api/latest",
            snapshot_path=str(target_file),
        )
        assert result["success"] is True
        assert target_file.exists()
        saved_data = json.loads(target_file.read_text(encoding="utf-8"))
        assert saved_data["total_products"] == 2


def test_sync_trend_coffee_catalog_network_error_safe_fallback(tmp_path):
    target_file = tmp_path / "menu_snapshot.json"
    initial_data = {"total_products": 1, "items": [{"name": "Cà phê sữa"}]}
    target_file.write_text(json.dumps(initial_data), encoding="utf-8")

    with patch("httpx.get", side_effect=httpx.ConnectError("Connection refused")):
        result = sync_trend_coffee_catalog(
            api_base="https://trendcoffee.net/api/latest",
            snapshot_path=str(target_file),
        )
        assert result["success"] is False
        assert "error" in result
        assert target_file.exists()
        saved_data = json.loads(target_file.read_text(encoding="utf-8"))
        assert saved_data["total_products"] == 1
        assert saved_data["items"][0]["name"] == "Cà phê sữa"


def test_register_trend_coffee_sync_job(tmp_path):
    store = SchedulerStore(tmp_path / "scheduler.db")
    scheduler = TaskScheduler(store=store)

    task = register_trend_coffee_sync_job(scheduler, interval_seconds=3600)
    assert task.schedule_type == "interval"
    assert task.schedule_value == "3600"
    assert task.tools == "http_request,file_write"
    assert task.metadata.get("merchant") == "trendcoffee"

    # Verify task was persisted in store
    retrieved = store.get_task(task.id)
    assert retrieved is not None
    assert retrieved["id"] == task.id
    scheduler.stop()
