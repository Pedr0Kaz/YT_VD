"""Descarrega vídeos do YouTube com o módulo oficial yt-dlp.

O YouTube não entrega um ficheiro único. O yt-dlp descobre os streams,
resolve o desafio JavaScript do player e o ffmpeg junta imagem e som.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError as YtDlpDownloadError


class DownloadError(Exception):
    """Erro mostrado ao utilizador, já em texto simples."""


@dataclass(frozen=True)
class Resolution:
    height: int | None
    label: str


@dataclass(frozen=True)
class VideoInfo:
    video_id: str
    title: str
    channel: str
    duration: int | None
    resolutions: list[Resolution]
    thumbnail: bytes | None = None


@dataclass(frozen=True)
class SavedDownload:
    path: Path
    resolution: str
    url: str


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _search_dirs() -> list[Path]:
    dirs: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(Path(meipass))
    dirs.append(app_dir())
    return dirs


def bundled_file(name: str) -> Path | None:
    for folder in _search_dirs():
        for candidate in (folder / "bin" / name, folder / name):
            if candidate.is_file():
                return candidate
    return None


def ensure_ffmpeg() -> str:
    """Usa o ffmpeg.exe que já está na pasta do programa."""
    found = bundled_file("ffmpeg.exe")
    if found:
        return str(found)

    dest = app_dir() / "bin" / "ffmpeg.exe"
    try:
        import imageio_ffmpeg
    except ImportError as exc:
        raise DownloadError("Falta o ficheiro bin\\ffmpeg.exe nesta pasta.") from exc

    source = Path(imageio_ffmpeg.get_ffmpeg_exe())
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    return str(dest)


def find_node() -> str:
    found = bundled_file("node.exe")
    if found:
        return str(found)

    on_path = shutil.which("node")
    if on_path:
        return on_path

    raise DownloadError("Falta o ficheiro bin\\node.exe nesta pasta.")


def format_selector(height: int | None) -> str:
    """Melhor vídeo até à altura pedida, mais o melhor áudio."""
    if height is None:
        return "bv*+ba/b"
    limit = f"[height<={height}]"
    return f"bv*{limit}+ba/b{limit}"


def available_browsers() -> list[str]:
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    found = []
    if (local / "Google" / "Chrome" / "User Data").is_dir():
        found.append("chrome")
    if (local / "Microsoft" / "Edge" / "User Data").is_dir():
        found.append("edge")
    return found


def install_cookie_copy_fix() -> None:
    """Copia a base de cookies mesmo com o Chrome ou o Edge abertos."""
    import yt_dlp.cookies as cookies

    if getattr(cookies, "_shared_copy_installed", False):
        return

    def _open_database_copy(database_path, tmpdir):
        database_copy_path = os.path.join(tmpdir, "temporary.sqlite")
        _copy_shared(database_path, database_copy_path)
        return sqlite3.connect(database_copy_path).cursor()

    cookies._open_database_copy = _open_database_copy
    cookies._shared_copy_installed = True


def _copy_shared(source, destination) -> None:
    import ctypes
    from ctypes import wintypes

    generic_read = 0x80000000
    share = 0x1 | 0x2 | 0x4
    open_existing = 3
    normal = 0x80
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.ReadFile.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    kernel32.ReadFile.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.CreateFileW(os.fspath(source), generic_read, share, None, open_existing, normal, None)
    if handle == wintypes.HANDLE(-1).value:
        raise PermissionError(ctypes.get_last_error(), "não foi possível ler a sessão do browser", source)
    try:
        with open(destination, "wb") as output:
            buffer = ctypes.create_string_buffer(1024 * 1024)
            read = wintypes.DWORD()
            while True:
                if not kernel32.ReadFile(handle, buffer, len(buffer), ctypes.byref(read), None):
                    raise OSError(ctypes.get_last_error(), "falhou a leitura da sessão do browser")
                if read.value == 0:
                    break
                output.write(buffer.raw[: read.value])
    finally:
        kernel32.CloseHandle(handle)


def _base_opts(
    output_dir: Path,
    height: int | None,
    hooks: list,
    browser: str | None = None,
    outtmpl: str | None = None,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    install_cookie_copy_fix()
    opts = {
        "format": format_selector(height),
        "merge_output_format": "mp4",
        "outtmpl": outtmpl or str(output_dir / "%(title)s [%(height)sp].%(ext)s"),
        "overwrites": False,
        "ffmpeg_location": ensure_ffmpeg(),
        "js_runtimes": {"node": {"path": find_node()}},
        "remote_components": [],
        "noplaylist": True,
        "windowsfilenames": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "concurrent_fragment_downloads": 48,
        "buffersize": 1024 * 1024,
        "retries": 10,
        "fragment_retries": 10,
        "progress_hooks": hooks,
    }
    if browser:
        opts["cookiesfrombrowser"] = (browser,)
    return opts


def _raise_from_ytdlp(exc: YtDlpDownloadError) -> None:
    message = str(exc).strip()
    while message.startswith("ERROR:"):
        message = message[6:].strip()
    lowered = message.lower()
    if "not a bot" in lowered or "sign in to confirm" in lowered:
        message = (
            "O YouTube pediu confirmação de que não és um robô. "
            "Inicia sessão no YouTube no Chrome ou no Edge e volta a analisar."
        )
    elif "could not copy chrome cookie database" in lowered:
        message = (
            "Não consegui ler a sessão do Chrome ou do Edge. "
            "Fecha o browser, ou escolhe o outro, e tenta outra vez."
        )
    raise DownloadError(message) from exc


def _download_thumbnail(url: str | None, video_id: str) -> bytes | None:
    candidates = []
    if video_id:
        candidates.append(f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg")
    if url:
        candidates.append(url)
    if video_id:
        candidates.append(f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg")
    for target in candidates:
        try:
            request = urllib.request.Request(target, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(request, timeout=15) as response:
                data = response.read(3_000_000)
        except Exception:
            continue
        if data and len(data) > 8_000:
            return data
    return None


def probe(url: str, browser: str | None = None) -> VideoInfo:
    opts = _base_opts(app_dir() / "bin", None, [], browser)
    opts["skip_download"] = True
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except YtDlpDownloadError as exc:
        _raise_from_ytdlp(exc)

    if info.get("is_live"):
        raise DownloadError("Este vídeo está em direto. Só é possível descarregar vídeos já publicados.")

    formats = info.get("formats") or []
    heights = sorted(
        {
            fmt.get("height")
            for fmt in formats
            if fmt.get("height") and fmt.get("vcodec") not in (None, "none")
        },
        reverse=True,
    )
    if not heights:
        raise DownloadError("Não foram encontradas resoluções de vídeo neste link.")

    duration = info.get("duration")
    resolutions = [
        Resolution(None, _resolution_label(None, heights[0], formats, duration)),
    ]
    resolutions.extend(
        Resolution(height, _resolution_label(height, height, formats, duration))
        for height in heights
    )

    video_id = info.get("id") or ""
    return VideoInfo(
        video_id=video_id,
        title=info.get("title") or "Sem título",
        channel=info.get("channel") or info.get("uploader") or "Canal desconhecido",
        duration=duration,
        resolutions=resolutions,
        thumbnail=_download_thumbnail(info.get("thumbnail"), video_id),
    )


def _unique_template(output_dir: Path, height: int | None, title: str | None) -> str:
    """Nome com a resolução, e um número extra se esse ficheiro já existir."""
    if not height:
        return str(output_dir / "%(title)s [%(height)sp].%(ext)s")

    label = f"{height}p"
    ydl = YoutubeDL(
        {
            "windowsfilenames": True,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "outtmpl": str(output_dir / f"%(title)s [{label}].%(ext)s"),
        }
    )
    if not title:
        return ydl.params["outtmpl"]["default"]

    template = ydl.params["outtmpl"]["default"]
    for index in range(1, 100):
        extra = "" if index == 1 else f" ({index})"
        template = str(output_dir / f"%(title)s [{label}]{extra}.%(ext)s")
        ydl.params["outtmpl"]["default"] = template
        prepared = Path(ydl.prepare_filename({"title": title, "ext": "mp4"}))
        if not prepared.exists():
            return template
    return template


def video_file_height(path: Path) -> int | None:
    """Lê a altura do vídeo já gravado, sem o voltar a descarregar."""
    if not path.is_file():
        return None
    try:
        completed = subprocess.run(
            [ensure_ffmpeg(), "-hide_banner", "-i", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"(\d{3,5})x(\d{3,5})", completed.stderr or "")
    if not match:
        return None
    return int(match.group(2))


def _saved_resolution(info: dict | None, height: int | None) -> str:
    value = None
    if info:
        value = info.get("height")
        if not value:
            for item in info.get("requested_downloads") or []:
                value = item.get("height") or value
    if isinstance(value, int) and value > 0:
        return f"{value}p"
    if height:
        return f"{height}p"
    return ""


def _saved_url(info: dict | None, url: str) -> str:
    video_id = (info or {}).get("id")
    if isinstance(video_id, str) and video_id:
        return f"https://www.youtube.com/watch?v={video_id}"
    page = (info or {}).get("webpage_url")
    if isinstance(page, str) and page.startswith("http"):
        return page.split("&", 1)[0]
    return url


def download(
    url: str,
    height: int | None,
    output_dir: Path,
    on_progress,
    browser: str | None = None,
    title: str | None = None,
) -> SavedDownload:
    final_path: dict[str, str] = {}
    meter = _SpeedMeter()
    last_emit = 0.0

    def hook(status: dict) -> None:
        nonlocal last_emit
        info = status.get("info_dict") or {}
        phase = _stream_phase(info)
        if status.get("status") == "downloading":
            downloaded = status.get("downloaded_bytes") or 0
            total = status.get("total_bytes") or status.get("total_bytes_estimate")
            percent = (downloaded / total * 100) if total else None
            speed = meter.update(phase, downloaded)
            now = time.monotonic()
            if now - last_emit < 0.5:
                return
            last_emit = now
            if speed is None:
                speed = status.get("speed")
            eta = status.get("eta")
            if speed and total and downloaded < total:
                eta = int((total - downloaded) / speed)
            on_progress(_progress_text(phase, downloaded, total, speed, eta), percent)
        elif status.get("status") == "finished":
            path = status.get("filename")
            if path:
                final_path["path"] = path
            last_emit = 0.0
            on_progress("A juntar vídeo e áudio…", None)

    template = _unique_template(output_dir, height, title)
    opts = _base_opts(output_dir, height, [hook], browser, template)
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except YtDlpDownloadError as exc:
        _raise_from_ytdlp(exc)

    saved = _finished_file(info, final_path.get("path"))
    if saved is None:
        raise DownloadError("O download terminou, mas o ficheiro final não foi encontrado.")
    on_progress("Download concluído.", 100)
    return SavedDownload(
        path=saved,
        resolution=_saved_resolution(info, height),
        url=_saved_url(info, url),
    )


def _finished_file(info: dict | None, hook_path: str | None) -> Path | None:
    requested = (info or {}).get("requested_downloads") or []
    for item in reversed(requested):
        filepath = item.get("filepath") or item.get("_filename")
        if filepath and Path(filepath).is_file():
            return Path(filepath)
    if hook_path and Path(hook_path).is_file():
        return Path(hook_path)
    return None


def format_duration(seconds: int | None) -> str:
    if not seconds:
        return "desconhecida"
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours} h {minutes:02d} min {secs:02d} s"
    return f"{minutes} min {secs:02d} s"


def _resolution_label(height: int | None, shown_height: int, formats: list, duration: int | None) -> str:
    name = "Melhor disponível" if height is None else f"{height}p"
    size = _estimate_size(formats, shown_height if height is None else height, duration)
    if size is None:
        return name
    return f"{name}  ·  {_format_size(size)}"


def _estimate_size(formats: list, height: int, duration: int | None) -> int | None:
    videos = [
        fmt
        for fmt in formats
        if fmt.get("height") == height and fmt.get("vcodec") not in (None, "none")
    ]
    if not videos:
        return None
    video = max(videos, key=lambda fmt: fmt.get("tbr") or 0)
    audios = [
        fmt
        for fmt in formats
        if fmt.get("vcodec") in (None, "none") and fmt.get("acodec") not in (None, "none")
    ]
    audio = max(audios, key=lambda fmt: fmt.get("abr") or fmt.get("tbr") or 0) if audios else None

    total = 0
    for fmt in (video, audio):
        if fmt is None:
            continue
        size = fmt.get("filesize") or fmt.get("filesize_approx")
        if size:
            total += int(size)
        elif duration and fmt.get("tbr"):
            total += int(fmt["tbr"] * 1000 / 8 * duration)
    return total or None


def _format_size(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            text = f"{value:.1f}".replace(".", ",")
            return f"cerca de {text} {unit}"
        value /= 1024
    return ""


def _stream_phase(info: dict) -> str:
    has_video = info.get("vcodec") not in (None, "none")
    has_audio = info.get("acodec") not in (None, "none")
    if has_video and not has_audio:
        return "Vídeo"
    if has_audio and not has_video:
        return "Áudio"
    return "Vídeo e áudio"


def _exact_size(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            text = f"{value:.1f}".replace(".", ",")
            return f"{text} {unit}"
        value /= 1024
    return ""


class _SpeedMeter:
    """Média da velocidade numa janela de alguns segundos, para não saltar a cada fragmento."""

    def __init__(self, window: float = 5.0) -> None:
        self.window = window
        self.phase: str | None = None
        self.samples: list[tuple[float, int]] = []

    def update(self, phase: str, downloaded: int) -> float | None:
        now = time.monotonic()
        if phase != self.phase or (self.samples and downloaded < self.samples[-1][1]):
            self.phase = phase
            self.samples.clear()
        self.samples.append((now, downloaded))
        cutoff = now - self.window
        while len(self.samples) > 2 and self.samples[0][0] < cutoff:
            self.samples.pop(0)
        if len(self.samples) < 2:
            return None
        started, start_bytes = self.samples[0]
        elapsed = now - started
        if elapsed < 1.0:
            return None
        return max(0.0, (downloaded - start_bytes) / elapsed)


def _format_rate(bytes_per_second: float) -> str:
    megabits = bytes_per_second * 8 / 1_000_000
    return f"{_exact_size(int(bytes_per_second))}/s ({megabits:.0f} Mbps)"


def _progress_text(phase: str, downloaded: int, total: int | None, speed: float | None, eta: int | None) -> str:
    parts = [phase]
    if total:
        parts.append(f"{_exact_size(downloaded)} de {_exact_size(total)}")
    elif downloaded:
        parts.append(_exact_size(downloaded))
    if speed:
        parts.append(_format_rate(speed))
    if eta is not None:
        parts.append(f"faltam {format_duration(eta)}")
    return "  ·  ".join(parts)
