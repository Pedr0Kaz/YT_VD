"""Pasta de destino e histórico de downloads, guardados entre sessões."""

from __future__ import annotations

import json
import os
from datetime import datetime
from io import BytesIO
from pathlib import Path

from PIL import Image


def data_dir() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "YouTubeDownloader"
    (root / "thumbs").mkdir(parents=True, exist_ok=True)
    return root


def _settings_path() -> Path:
    return data_dir() / "settings.json"


def _history_path() -> Path:
    return data_dir() / "history.json"


def _read_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def load_output_dir() -> str | None:
    value = _read_json(_settings_path(), {}).get("output_dir")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def save_output_dir(folder: str) -> None:
    settings = _read_json(_settings_path(), {})
    if not isinstance(settings, dict):
        settings = {}
    settings["output_dir"] = folder
    _write_json(_settings_path(), settings)


def load_browser() -> str | None:
    value = _read_json(_settings_path(), {}).get("browser")
    if value in ("chrome", "edge", "none"):
        return value
    return None


def save_browser(browser: str) -> None:
    settings = _read_json(_settings_path(), {})
    if not isinstance(settings, dict):
        settings = {}
    settings["browser"] = browser
    _write_json(_settings_path(), settings)


def load_history() -> list[dict]:
    items = _read_json(_history_path(), [])
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def record_download(
    *,
    title: str,
    path: Path,
    video_id: str,
    thumbnail: bytes | None,
    finished_at: datetime | None = None,
    resolution: str = "",
    url: str = "",
) -> None:
    when = finished_at or datetime.now().astimezone()
    thumb_name = _store_thumbnail(video_id or path.stem, thumbnail)
    entry = {
        "title": title,
        "path": str(path),
        "video_id": video_id,
        "finished_at": when.isoformat(timespec="seconds"),
        "thumb": thumb_name,
        "resolution": resolution,
        "url": url or (f"https://www.youtube.com/watch?v={video_id}" if video_id else ""),
    }
    items = [item for item in load_history() if item.get("path") != entry["path"]]
    items.insert(0, entry)
    _write_json(_history_path(), items[:40])


def youtube_url(item: dict) -> str:
    url = item.get("url")
    if isinstance(url, str) and url.startswith("http"):
        return url
    video_id = item.get("video_id")
    if isinstance(video_id, str) and video_id:
        return f"https://www.youtube.com/watch?v={video_id}"
    return ""


def annotate_download(path: Path, *, resolution: str = "", url: str = "") -> bool:
    """Preenche resolução e link em downloads antigos, sem os voltar a gravar."""
    items = load_history()
    changed = False
    target = str(path)
    for item in items:
        if item.get("path") != target:
            continue
        if resolution and not item.get("resolution"):
            item["resolution"] = resolution
            changed = True
        if url and not item.get("url"):
            item["url"] = url
            changed = True
    if changed:
        _write_json(_history_path(), items)
    return changed


def thumb_path(name: str | None) -> Path | None:
    if not name:
        return None
    path = data_dir() / "thumbs" / name
    return path if path.is_file() else None


def format_when(value: str | None) -> str:
    if not value:
        return ""
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return value
    return moment.strftime("%d/%m/%Y  %H:%M")


def _store_thumbnail(key: str, data: bytes | None) -> str | None:
    if not data:
        return None
    safe = "".join(char if char.isalnum() or char in "-_" else "_" for char in key)[:80] or "video"
    name = f"{safe}.jpg"
    destination = data_dir() / "thumbs" / name
    try:
        image = Image.open(BytesIO(data))
        image.thumbnail((320, 180), Image.Resampling.LANCZOS)
        image.convert("RGB").save(destination, format="JPEG", quality=85)
    except Exception:
        return None
    return name
