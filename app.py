"""Programa Windows para descarregar vídeos do YouTube com o yt-dlp."""

from __future__ import annotations

import os
import threading
import time
import tkinter as tk
from datetime import datetime
from io import BytesIO
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from downloader import (
    DownloadError,
    DownloadPaused,
    MergeStopped,
    Resolution,
    SavedDownload,
    SplitDownload,
    VideoInfo,
    discard_partials,
    find_split_downloads,
    hold_output,
    hold_path,
    merge_streams,
    release_path,
    _download_thumbnail,
    available_browsers,
    download,
    format_duration,
    probe,
    video_file_height,
)
from library import (
    annotate_download,
    format_when,
    load_active_jobs,
    load_browser,
    load_history,
    load_output_dir,
    record_download,
    save_active_jobs,
    save_browser,
    save_output_dir,
    store_thumbnail,
    thumb_path,
    youtube_url,
)

BG = "#14161a"
CARD = "#1e222a"
FG = "#f2f3f5"
MUTED = "#a0a6b0"
ACCENT = "#e11d2e"
ENTRY = "#0e1014"
OK = "#3dd68c"
JOB = "#262b33"
MAX_DOWNLOADS = 3


class _ActiveJob:
    def __init__(
        self,
        job_id: int,
        video: VideoInfo,
        url: str,
        height: int | None,
        folder: str,
        browser: str | None,
        resolution: str,
    ) -> None:
        self.job_id = job_id
        self.video = video
        self.url = url
        self.height = height
        self.folder = folder
        self.browser = browser
        self.resolution = resolution
        self.state = "queued"
        self.outtmpl: str | None = None
        self.percent_value: float | None = None
        self.thumb_name: str | None = None
        self.pause_event = threading.Event()
        self.row: tk.Frame | None = None
        self.progress: ttk.Progressbar | None = None
        self.percent: tk.Label | None = None
        self.detail: tk.Label | None = None
        self.action: tk.Button | None = None
        self.remove_button: tk.Button | None = None
        self.photo: ImageTk.PhotoImage | None = None
        self.split: SplitDownload | None = None


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("YouTube Downloader")
        self.geometry("1280x820")
        self.minsize(1100, 720)
        self.configure(bg=BG)

        self.video: VideoInfo | None = None
        self.resolutions: list[Resolution] = []
        self.probing = False
        self._thumb_image: ImageTk.PhotoImage | None = None
        self._history_images: list[ImageTk.PhotoImage] = []
        self._jobs: dict[int, _ActiveJob] = {}
        self._queue: list[_ActiveJob] = []
        self._running = 0
        self._next_job = 1
        self._closing = False
        self._last_active_save = 0.0
        self._merging = 0
        self._pending_merges: list[SplitDownload] = []

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.output_var.set(load_output_dir() or str(Path.home() / "Downloads" / "YouTube"))
        self.output_var.trace_add("write", self._persist_output_dir)
        self._refresh_history()
        self._restore_active()
        threading.Thread(target=self._import_previous_download, daemon=True).start()
        threading.Thread(target=self._scan_splits, args=(self.output_var.get().strip(),), daemon=True).start()

    def _build(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("TLabel", background=BG, foreground=FG, font=("Segoe UI", 10))
        style.configure("Card.TLabel", background=CARD, foreground=FG, font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("Title.TLabel", background=BG, foreground=FG, font=("Segoe UI", 18, "bold"))
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"), padding=(14, 8))
        style.configure("TButton", font=("Segoe UI", 10), padding=(12, 7))
        style.configure(
            "TEntry",
            fieldbackground=ENTRY,
            foreground=FG,
            insertcolor=FG,
            padding=8,
        )
        style.configure(
            "TCombobox",
            fieldbackground=ENTRY,
            foreground=FG,
            padding=6,
        )
        style.map("TCombobox", fieldbackground=[("readonly", ENTRY)])
        style.configure(
            "red.Horizontal.TProgressbar",
            troughcolor="#2a2e36",
            background=ACCENT,
            bordercolor=BG,
            lightcolor=ACCENT,
            darkcolor=ACCENT,
        )

        shell = ttk.Frame(self, padding=18)
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(0, weight=1)

        root = ttk.Frame(shell)
        root.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        root.columnconfigure(0, weight=1)
        self._build_side(shell)

        ttk.Label(root, text="YouTube Downloader", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            root,
            text="Cola o link, escolhe a resolução e descarrega. Vários vídeos podem descarregar ao mesmo tempo.",
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 16))

        ttk.Label(root, text="Link do YouTube").grid(row=2, column=0, sticky="w")
        link_row = ttk.Frame(root)
        link_row.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        link_row.columnconfigure(0, weight=1)

        self.url_var = tk.StringVar()
        self.url_entry = ttk.Entry(link_row, textvariable=self.url_var, font=("Segoe UI", 11))
        self.url_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8), ipady=4)
        self.url_entry.bind("<Return>", lambda _event: self.on_probe())
        self.probe_button = ttk.Button(link_row, text="Analisar", command=self.on_probe)
        self.probe_button.grid(row=0, column=1)

        card = ttk.Frame(root, style="Card.TFrame", padding=14)
        card.grid(row=4, column=0, sticky="ew", pady=16)
        card.columnconfigure(1, weight=1)

        self.thumb_label = tk.Label(card, bg=CARD, bd=0)
        self.thumb_label.grid(row=0, column=0, rowspan=3, sticky="nw", padx=(0, 14))

        self.title_var = tk.StringVar(value="Ainda sem vídeo")
        self.channel_var = tk.StringVar(value="Cola um link do YouTube e carrega em Analisar.")
        self.duration_var = tk.StringVar(value="")
        ttk.Label(
            card,
            textvariable=self.title_var,
            style="Card.TLabel",
            font=("Segoe UI", 12, "bold"),
            wraplength=480,
        ).grid(row=0, column=1, sticky="nw")
        ttk.Label(card, textvariable=self.channel_var, style="Card.TLabel", foreground=MUTED).grid(
            row=1, column=1, sticky="nw", pady=(4, 0)
        )
        ttk.Label(card, textvariable=self.duration_var, style="Card.TLabel", foreground=MUTED).grid(
            row=2, column=1, sticky="nw"
        )

        options = ttk.Frame(root)
        options.grid(row=5, column=0, sticky="ew")
        options.columnconfigure(1, weight=1)

        ttk.Label(options, text="Resolução").grid(row=0, column=0, sticky="w", pady=4)
        self.resolution_var = tk.StringVar()
        self.resolution_box = ttk.Combobox(
            options,
            textvariable=self.resolution_var,
            state="readonly",
            font=("Segoe UI", 10),
        )
        self.resolution_box.grid(row=0, column=1, sticky="ew", padx=(12, 0), pady=4)

        ttk.Label(options, text="Pasta").grid(row=1, column=0, sticky="w", pady=4)
        folder_row = ttk.Frame(options)
        folder_row.grid(row=1, column=1, sticky="ew", padx=(12, 0), pady=4)
        folder_row.columnconfigure(0, weight=1)
        self.output_var = tk.StringVar()
        ttk.Entry(folder_row, textvariable=self.output_var).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ttk.Button(folder_row, text="Escolher", command=self.on_choose_folder).grid(row=0, column=1)

        ttk.Label(options, text="Sessão").grid(row=2, column=0, sticky="w", pady=4)
        self.browser_var = tk.StringVar()
        self._browser_by_label = {"Sem sessão": None}
        browser_labels = []
        for key in available_browsers():
            label = "Chrome" if key == "chrome" else "Edge"
            self._browser_by_label[label] = key
            browser_labels.append(label)
        browser_labels.append("Sem sessão")
        self.browser_box = ttk.Combobox(
            options,
            textvariable=self.browser_var,
            state="readonly",
            values=browser_labels,
            font=("Segoe UI", 10),
        )
        self.browser_box.grid(row=2, column=1, sticky="ew", padx=(12, 0), pady=4)
        saved_browser = load_browser()
        if saved_browser == "chrome" and "Chrome" in browser_labels:
            self.browser_var.set("Chrome")
        elif saved_browser == "edge" and "Edge" in browser_labels:
            self.browser_var.set("Edge")
        else:
            self.browser_var.set("Sem sessão")
        self.browser_var.trace_add("write", lambda *_args: save_browser(self._browser_setting()))

        actions = ttk.Frame(root)
        actions.grid(row=6, column=0, sticky="ew", pady=(16, 0))
        self.download_button = tk.Button(
            actions,
            text="Descarregar",
            command=self.on_download,
            bg=ACCENT,
            fg="white",
            activebackground="#ff3b4e",
            activeforeground="white",
            relief="flat",
            font=("Segoe UI", 11, "bold"),
            padx=18,
            pady=8,
            cursor="hand2",
            state="disabled",
        )
        self.download_button.pack(side="left")
        ttk.Button(actions, text="Abrir pasta", command=self.on_open_folder).pack(side="left", padx=(8, 0))

        self.status_var = tk.StringVar(value="Pronto. Cola um link e carrega em Analisar.")
        ttk.Label(root, textvariable=self.status_var, style="Muted.TLabel", wraplength=640).grid(
            row=7, column=0, sticky="w", pady=(12, 0)
        )
        ttk.Label(
            root,
            text="O áudio é juntado ao vídeo. Motor: yt-dlp.",
            style="Muted.TLabel",
        ).grid(row=8, column=0, sticky="w", pady=(18, 0))

    def _build_side(self, shell: ttk.Frame) -> None:
        side = ttk.Frame(shell)
        side.grid(row=0, column=1, sticky="nsew")
        side.rowconfigure(0, weight=1)
        side.rowconfigure(1, weight=1)
        side.columnconfigure(0, weight=1)
        self._build_active(side)
        self._build_history(side)

    def _build_active(self, side: ttk.Frame) -> None:
        panel = ttk.Frame(side, style="Card.TFrame", padding=12)
        panel.grid(row=0, column=0, sticky="nsew", pady=(0, 10))
        panel.rowconfigure(2, weight=1)
        panel.columnconfigure(0, weight=1)
        ttk.Label(panel, text="A descarregar", style="Card.TLabel", font=("Segoe UI", 12, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            panel,
            text="Até 3 em simultâneo. Podes pausar e continuar depois.",
            style="Card.TLabel",
            foreground=MUTED,
            font=("Segoe UI", 8),
        ).grid(row=1, column=0, sticky="w", pady=(0, 8))
        self.active_canvas = tk.Canvas(panel, bg=CARD, highlightthickness=0, width=300, bd=0)
        self.active_canvas.grid(row=2, column=0, sticky="nsew")
        self.active_inner = ttk.Frame(self.active_canvas, style="Card.TFrame")
        self.active_window = self.active_canvas.create_window((0, 0), window=self.active_inner, anchor="nw")
        self.active_inner.bind("<Configure>", lambda _event: self._canvas_fit(self.active_canvas))
        self.active_canvas.bind("<Configure>", lambda event: self.active_canvas.itemconfigure(self.active_window, width=event.width))
        self.active_canvas.bind("<Enter>", lambda _event: self.active_canvas.bind_all("<MouseWheel>", self._active_wheel))
        self.active_canvas.bind("<Leave>", lambda _event: self.active_canvas.unbind_all("<MouseWheel>"))
        self.active_inner.columnconfigure(0, weight=1)
        self._show_active_empty()

    def _build_history(self, side: ttk.Frame) -> None:
        side = ttk.Frame(side, style="Card.TFrame", padding=12)
        side.grid(row=1, column=0, sticky="nsew")
        side.rowconfigure(1, weight=1)
        side.columnconfigure(0, weight=1)

        ttk.Label(side, text="Últimos downloads", style="Card.TLabel", font=("Segoe UI", 12, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 10)
        )
        self.history_canvas = tk.Canvas(side, bg=CARD, highlightthickness=0, width=300, bd=0)
        self.history_canvas.grid(row=1, column=0, sticky="nsew")
        self.history_inner = ttk.Frame(self.history_canvas, style="Card.TFrame")
        self.history_window = self.history_canvas.create_window((0, 0), window=self.history_inner, anchor="nw")
        self.history_inner.bind("<Configure>", self._history_resized)
        self.history_canvas.bind("<Configure>", self._history_canvas_resized)
        self.history_canvas.bind("<Enter>", lambda _event: self.history_canvas.bind_all("<MouseWheel>", self._history_wheel))
        self.history_canvas.bind("<Leave>", lambda _event: self.history_canvas.unbind_all("<MouseWheel>"))

    def _canvas_fit(self, canvas: tk.Canvas) -> None:
        canvas.configure(scrollregion=canvas.bbox("all"))

    def _active_wheel(self, event) -> None:
        self.active_canvas.yview_scroll(int(-event.delta / 120), "units")

    def _history_resized(self, _event) -> None:
        self._canvas_fit(self.history_canvas)

    def _history_canvas_resized(self, event) -> None:
        self.history_canvas.itemconfigure(self.history_window, width=event.width)

    def _history_wheel(self, event) -> None:
        self.history_canvas.yview_scroll(int(-event.delta / 120), "units")

    def _persist_output_dir(self, *_args) -> None:
        folder = self.output_var.get().strip()
        if folder:
            save_output_dir(folder)

    def _refresh_history(self) -> None:
        for child in self.history_inner.winfo_children():
            child.destroy()
        self._history_images.clear()
        items = load_history()
        if not items:
            ttk.Label(
                self.history_inner,
                text="Ainda não há downloads.",
                style="Card.TLabel",
                foreground=MUTED,
                wraplength=250,
            ).grid(row=0, column=0, sticky="w")
            return
        for index, item in enumerate(items):
            self._add_history_row(index, item)

    def _add_history_row(self, index: int, item: dict) -> None:
        row = tk.Frame(self.history_inner, bg=CARD, cursor="hand2")
        row.grid(row=index, column=0, sticky="ew", pady=(0, 12))
        image_label = tk.Label(row, bg=CARD, bd=0)
        image_label.grid(row=0, column=0, rowspan=3, sticky="nw", padx=(0, 8))
        picture = self._history_photo(item.get("thumb"))
        if picture is not None:
            image_label.configure(image=picture)
        title = tk.Label(
            row,
            text=item.get("title") or "Vídeo",
            bg=CARD,
            fg=FG,
            font=("Segoe UI", 9, "bold"),
            wraplength=160,
            justify="left",
            anchor="w",
        )
        title.grid(row=0, column=1, sticky="nw")
        resolution = (item.get("resolution") or "").strip()
        clickable = [row, image_label, title]
        line = 1
        if resolution:
            resolution_label = tk.Label(
                row,
                text=resolution,
                bg=CARD,
                fg=FG,
                font=("Segoe UI", 8, "bold"),
                anchor="w",
            )
            resolution_label.grid(row=line, column=1, sticky="nw")
            clickable.append(resolution_label)
            line += 1
        when_label = tk.Label(
            row,
            text=format_when(item.get("finished_at")),
            bg=CARD,
            fg=MUTED,
            font=("Segoe UI", 8),
            anchor="w",
        )
        when_label.grid(row=line, column=1, sticky="nw")
        clickable.append(when_label)
        line += 1
        link = youtube_url(item)
        if link:
            use_link = tk.Button(
                row,
                text="Usar link",
                command=lambda target=link: self._reuse_link(target),
                bg=CARD,
                fg=ACCENT,
                activebackground=CARD,
                activeforeground=FG,
                relief="flat",
                bd=0,
                padx=0,
                pady=0,
                font=("Segoe UI", 8, "underline"),
                cursor="hand2",
            )
            use_link.grid(row=line, column=1, sticky="w", pady=(2, 0))
        image_label.grid_configure(rowspan=line + 1)
        for widget in clickable:
            widget.bind("<Button-1>", lambda _event, path=item.get("path"): self._open_download(path))

    def _history_photo(self, name: str | None) -> ImageTk.PhotoImage | None:
        path = thumb_path(name)
        if path is None:
            return None
        try:
            image = Image.open(path)
            image.thumbnail((112, 63), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(image)
        except Exception:
            return None
        self._history_images.append(photo)
        return photo

    def _open_download(self, path: str | None) -> None:
        if not path or not Path(path).is_file():
            messagebox.showinfo("Ficheiro em falta", "Este download já não está na pasta.")
            return
        os.startfile(path)  # noqa: S606 - ficheiro que o próprio programa gravou

    def _import_previous_download(self) -> None:
        previous = Path(r"E:\WarDogs") / "WARDOGS now over 3 MILLION PLAYERS.mp4"
        if previous.is_file() and not any(item.get("path") == str(previous) for item in load_history()):
            record_download(
                title=previous.stem,
                path=previous,
                video_id="RBmwAZdnmVY",
                thumbnail=_download_thumbnail(None, "RBmwAZdnmVY"),
                finished_at=datetime.fromtimestamp(previous.stat().st_mtime).astimezone(),
                url="https://www.youtube.com/watch?v=RBmwAZdnmVY",
            )
            self.after(0, self._refresh_history)
        self._fill_saved_details()

    def _fill_saved_details(self) -> None:
        changed = False
        for item in load_history():
            raw_path = item.get("path")
            if not isinstance(raw_path, str) or not raw_path:
                continue
            path = Path(raw_path)
            resolution = (item.get("resolution") or "").strip()
            if not resolution and path.is_file():
                height = video_file_height(path)
                if height:
                    resolution = f"{height}p"
            url = youtube_url(item)
            if annotate_download(path, resolution=resolution, url=url):
                changed = True
        if changed:
            self.after(0, self._refresh_history)

    def on_choose_folder(self) -> None:
        current = self.output_var.get().strip() or str(Path.home() / "Downloads")
        chosen = filedialog.askdirectory(initialdir=current)
        if chosen:
            self.output_var.set(chosen)

    def on_open_folder(self) -> None:
        folder = Path(self.output_var.get().strip() or ".")
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)  # noqa: S606 - pasta escolhida pelo utilizador no Windows

    def on_probe(self) -> None:
        if self.probing:
            return
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("Link em falta", "Cola o link do vídeo.")
            return
        self.probing = True
        self._show_thumbnail(None)
        self.probe_button.state(["disabled"])
        self.download_button.configure(state="disabled")
        self.status_var.set("A analisar o vídeo…")
        browser = self._selected_browser()

        def runner() -> None:
            try:
                result: VideoInfo | BaseException = probe(url, browser)
            except Exception as exc:  # noqa: BLE001 - mostrado na janela
                result = exc
            self.after(0, lambda: self._probe_finished(result))

        threading.Thread(target=runner, daemon=True).start()

    def _probe_finished(self, result: VideoInfo | BaseException) -> None:
        self.probing = False
        self.probe_button.state(["!disabled"])
        self._probe_done(result)

    def _probe_done(self, result: VideoInfo | BaseException) -> None:
        if isinstance(result, BaseException):
            self.download_button.configure(state="normal" if self.video is not None else "disabled")
            self._show_error(result)
            return
        self.video = result
        self.resolutions = result.resolutions
        self.title_var.set(result.title)
        self.channel_var.set(result.channel)
        self.duration_var.set(f"Duração: {format_duration(result.duration)}")
        self._show_thumbnail(result.thumbnail)
        labels = [item.label for item in result.resolutions]
        self.resolution_box["values"] = labels
        preferred = next((item.label for item in result.resolutions if item.height == 1080), labels[0] if labels else "")
        self.resolution_var.set(preferred)
        self.status_var.set("Escolhe a resolução e carrega em Descarregar. O som vem incluído.")
        self.download_button.configure(state="normal")

    def on_download(self) -> None:
        if self.probing:
            return
        video = self.video
        if video is None:
            messagebox.showwarning("Sem vídeo", "Analisa o link antes de descarregar.")
            return
        url = self.url_var.get().strip()
        folder = self.output_var.get().strip()
        if not folder:
            messagebox.showwarning("Pasta em falta", "Escolhe a pasta onde o vídeo vai ficar.")
            return
        height = self._selected_height()
        resolution = f"{height}p" if height else "Melhor"
        job = _ActiveJob(
            job_id=self._next_job,
            video=video,
            url=url,
            height=height,
            folder=folder,
            browser=self._selected_browser(),
            resolution=resolution,
        )
        self._next_job += 1
        self._jobs[job.job_id] = job
        self._queue.append(job)
        self._mount_job(job)
        self._paint_job(job.job_id, "À espera", 0, None)
        self._save_active()
        waiting = max(0, len(self._queue) + self._running - MAX_DOWNLOADS)
        if waiting:
            self.status_var.set(f"{video.title} ficou na fila.")
        else:
            self.status_var.set(f"A descarregar {video.title}. Podes começar outro.")
        self._start_ready_jobs()

    def _selected_browser(self) -> str | None:
        return self._browser_by_label.get(self.browser_var.get())

    def _browser_setting(self) -> str:
        browser = self._selected_browser()
        return browser if browser else "none"

    def _selected_height(self) -> int | None:
        label = self.resolution_var.get()
        for item in self.resolutions:
            if item.label == label:
                return item.height
        return None

    def _reuse_link(self, url: str) -> None:
        if not url:
            return
        self.url_var.set(url)
        self.url_entry.focus_set()
        self.url_entry.icursor("end")
        self.url_entry.selection_range(0, "end")
        same_video = self.video is not None and self.video.video_id and self.video.video_id in url
        if same_video:
            self.status_var.set("Link colocado. Escolhe outra resolução e descarrega.")
            return
        self.status_var.set("Link colocado. Carrega em Analisar para escolher a resolução.")

    def _show_active_empty(self) -> None:
        if self._jobs:
            return
        for child in self.active_inner.winfo_children():
            child.destroy()
        ttk.Label(
            self.active_inner,
            text="Nenhum download a decorrer.",
            style="Card.TLabel",
            foreground=MUTED,
            wraplength=270,
        ).grid(row=0, column=0, sticky="w")

    def _mount_job(self, job: _ActiveJob) -> None:
        if len(self._jobs) == 1:
            for child in self.active_inner.winfo_children():
                child.destroy()
        row = tk.Frame(self.active_inner, bg=JOB, padx=8, pady=8)
        row.grid(row=job.job_id, column=0, sticky="ew", pady=(0, 8))
        row.columnconfigure(1, weight=1)
        image_label = tk.Label(row, bg=JOB, bd=0)
        image_label.grid(row=0, column=0, rowspan=5, sticky="nw", padx=(0, 8))
        job.photo = self._job_photo(job.video.thumbnail)
        if job.photo is not None:
            image_label.configure(image=job.photo)
        title = tk.Label(
            row,
            text=job.video.title,
            bg=JOB,
            fg=FG,
            font=("Segoe UI", 9, "bold"),
            wraplength=170,
            justify="left",
            anchor="w",
        )
        title.grid(row=0, column=1, sticky="ew")
        job.percent = tk.Label(row, text="—", bg=JOB, fg=FG, font=("Segoe UI", 9, "bold"))
        job.percent.grid(row=0, column=2, sticky="ne", padx=(6, 0))
        tk.Label(
            row,
            text=job.resolution,
            bg=JOB,
            fg=MUTED,
            font=("Segoe UI", 8),
            anchor="w",
        ).grid(row=1, column=1, columnspan=2, sticky="w")
        job.progress = ttk.Progressbar(row, style="red.Horizontal.TProgressbar", mode="determinate", maximum=100)
        job.progress.grid(row=2, column=1, columnspan=2, sticky="ew", pady=(4, 2))
        job.detail = tk.Label(
            row,
            text="À espera",
            bg=JOB,
            fg=MUTED,
            font=("Segoe UI", 8),
            wraplength=190,
            justify="left",
            anchor="w",
        )
        job.detail.grid(row=3, column=1, columnspan=2, sticky="w")
        actions = tk.Frame(row, bg=JOB)
        actions.grid(row=4, column=1, columnspan=2, sticky="w", pady=(2, 0))
        job.action = tk.Button(
            actions,
            text="Pausar",
            command=lambda: self._pause_job(job.job_id),
            bg=JOB,
            fg=ACCENT,
            activebackground=JOB,
            activeforeground=FG,
            relief="flat",
            bd=0,
            padx=0,
            font=("Segoe UI", 8, "underline"),
            cursor="hand2",
        )
        job.action.pack(side="left")
        job.remove_button = tk.Button(
            actions,
            text="Remover",
            command=lambda: self._dismiss_job(job.job_id),
            bg=JOB,
            fg=MUTED,
            activebackground=JOB,
            activeforeground=FG,
            relief="flat",
            bd=0,
            padx=0,
            font=("Segoe UI", 8, "underline"),
            cursor="hand2",
        )
        job.row = row
        self._sync_action(job)
        self._canvas_fit(self.active_canvas)

    def _job_photo(self, data: bytes | None) -> ImageTk.PhotoImage | None:
        if not data:
            return None
        try:
            image = Image.open(BytesIO(data))
            image.thumbnail((96, 54), Image.Resampling.LANCZOS)
            return ImageTk.PhotoImage(image)
        except Exception:
            return None

    def _start_ready_jobs(self) -> None:
        if self._closing:
            return
        while self._running < MAX_DOWNLOADS and self._queue:
            job = self._queue.pop(0)
            job.state = "running"
            job.pause_event.clear()
            self._running += 1
            self._sync_action(job)
            self._paint_job(job.job_id, "A iniciar…", job.percent_value, None)
            threading.Thread(target=self._run_job, args=(job,), daemon=True).start()

    def _run_job(self, job: _ActiveJob) -> None:
        def report(message: str, percent: float | None, rate: str | None = None) -> None:
            self.after(0, lambda m=message, p=percent, s=rate: self._paint_job(job.job_id, m, p, s))

        def prepared(template: str) -> None:
            job.outtmpl = template
            self.after(0, self._save_active)

        try:
            result: SavedDownload | BaseException = download(
                job.url,
                job.height,
                Path(job.folder),
                report,
                job.browser,
                job.video.title,
                job.outtmpl,
                job.pause_event,
                prepared,
            )
        except Exception as exc:  # noqa: BLE001 - pausa ou erro mostrado na linha
            result = exc
        self.after(0, lambda finished=result: self._job_finished(job.job_id, finished))

    def _paint_job(self, job_id: int, message: str, percent: float | None, rate: str | None) -> None:
        job = self._jobs.get(job_id)
        if job is None or job.progress is None or job.percent is None or job.detail is None:
            return
        if job.state == "paused":
            return
        if percent is not None:
            job.percent_value = percent
            now = time.monotonic()
            if now - self._last_active_save > 3:
                self._save_active()
        if job.state == "pausing":
            return
        if percent is None:
            if str(job.progress["mode"]) != "indeterminate":
                job.progress.configure(mode="indeterminate")
                job.progress.start(12)
            job.percent.configure(text="…")
        else:
            if str(job.progress["mode"]) != "determinate":
                job.progress.stop()
                job.progress.configure(mode="determinate")
            job.progress["value"] = percent
            job.percent.configure(text=f"{percent:.0f}%")
        job.detail.configure(text=rate or message, fg=MUTED)

    def _sync_action(self, job: _ActiveJob) -> None:
        if job.action is None:
            return
        job.action.configure(state="normal")
        if job.state == "merging":
            job.action.pack_forget()
            if job.remove_button is not None:
                job.remove_button.pack_forget()
            return
        job.action.pack(side="left")
        if job.state == "paused":
            job.action.configure(text="Continuar", command=lambda jid=job.job_id: self._resume_job(jid))
            if job.remove_button is not None:
                job.remove_button.pack(side="left", padx=(10, 0))
            return
        if job.remove_button is not None:
            job.remove_button.pack_forget()
        if job.state == "error":
            job.action.configure(text="Fechar", command=lambda jid=job.job_id: self._dismiss_job(jid))
            return
        job.action.configure(text="Pausar", command=lambda jid=job.job_id: self._pause_job(jid))

    def _show_paused(self, job: _ActiveJob, announce: bool = True) -> None:
        if job.progress is not None:
            job.progress.stop()
            job.progress.configure(mode="determinate")
            job.progress["value"] = job.percent_value or 0
        if job.percent is not None:
            job.percent.configure(text="—" if job.percent_value is None else f"{job.percent_value:.0f}%")
        if job.detail is not None:
            job.detail.configure(text="Em pausa", fg=MUTED)
        self._sync_action(job)
        if announce:
            self.status_var.set(f"{job.video.title} em pausa.")

    def _pause_job(self, job_id: int) -> None:
        job = self._jobs.get(job_id)
        if job is None or job.state in ("paused", "error", "pausing", "merging"):
            return
        if job.state == "queued":
            self._queue = [item for item in self._queue if item.job_id != job_id]
            job.state = "paused"
            self._show_paused(job)
            self._save_active()
            return
        job.state = "pausing"
        job.pause_event.set()
        if job.detail is not None:
            job.detail.configure(text="A pausar…", fg=MUTED)
        if job.action is not None:
            job.action.configure(state="disabled")

    def _resume_job(self, job_id: int) -> None:
        job = self._jobs.get(job_id)
        if job is None or job.state != "paused" or self._closing:
            return
        job.state = "queued"
        job.pause_event.clear()
        if job not in self._queue:
            self._queue.append(job)
        self._sync_action(job)
        if job.detail is not None:
            job.detail.configure(text="À espera", fg=MUTED)
        if job.percent_value is not None and job.progress is not None and job.percent is not None:
            job.progress.configure(mode="determinate")
            job.progress["value"] = job.percent_value
            job.percent.configure(text=f"{job.percent_value:.0f}%")
        self._save_active()
        self.status_var.set(f"A continuar {job.video.title}.")
        self._start_ready_jobs()

    def _record_job(self, job: _ActiveJob) -> dict:
        if not job.thumb_name and job.video.thumbnail:
            job.thumb_name = store_thumbnail(job.video.video_id or job.video.title, job.video.thumbnail)
        return {
            "id": job.job_id,
            "url": job.url,
            "title": job.video.title,
            "video_id": job.video.video_id,
            "channel": job.video.channel,
            "height": job.height,
            "resolution": job.resolution,
            "folder": job.folder,
            "browser": job.browser,
            "outtmpl": job.outtmpl,
            "percent": job.percent_value,
            "thumb": job.thumb_name,
        }

    def _save_active(self) -> None:
        self._last_active_save = time.monotonic()
        save_active_jobs(
            [self._record_job(job) for job in self._jobs.values() if job.state not in ("error", "merging")]
        )

    def _restore_active(self) -> None:
        items = load_active_jobs()
        if not items:
            return
        for item in items:
            job_id = item.get("id")
            if not isinstance(job_id, int):
                continue
            thumb_bytes = None
            stored = item.get("thumb") if isinstance(item.get("thumb"), str) else None
            thumb = thumb_path(stored)
            if thumb is not None:
                try:
                    thumb_bytes = thumb.read_bytes()
                except OSError:
                    thumb_bytes = None
            height = item.get("height") if isinstance(item.get("height"), int) else None
            video = VideoInfo(
                str(item.get("video_id") or ""),
                str(item.get("title") or "Vídeo"),
                str(item.get("channel") or ""),
                None,
                [],
                thumb_bytes,
            )
            browser = item.get("browser") if item.get("browser") in ("chrome", "edge") else None
            job = _ActiveJob(
                job_id,
                video,
                str(item.get("url") or ""),
                height,
                str(item.get("folder") or ""),
                browser,
                str(item.get("resolution") or (f"{height}p" if height else "Melhor")),
            )
            job.outtmpl = item.get("outtmpl") if isinstance(item.get("outtmpl"), str) else None
            job.thumb_name = stored
            percent = item.get("percent")
            job.percent_value = float(percent) if isinstance(percent, (int, float)) else None
            job.state = "paused"
            hold_output(job.outtmpl, job.video.title)
            self._jobs[job.job_id] = job
            self._mount_job(job)
            self._show_paused(job, announce=False)
        if self._jobs:
            self._next_job = max(self._jobs) + 1
            self.status_var.set("Há downloads em pausa. Carrega em Continuar.")

    def _scan_splits(self, folder: str) -> None:
        try:
            found = find_split_downloads(Path(folder))
        except Exception as exc:  # noqa: BLE001 - mostrado na linha de estado
            self.after(0, lambda: self.status_var.set(f"Não consegui procurar ficheiros por juntar: {exc}"))
            return
        self.after(0, lambda items=found: self._queue_merges(items))

    def _queue_merges(self, items: list[SplitDownload]) -> None:
        if self._closing or not items:
            return
        self._pending_merges.extend(items)
        self.status_var.set("Encontrei vídeo e áudio separados. Vou juntá-los.")
        self._start_next_merge()

    def _start_next_merge(self) -> None:
        if self._closing or self._merging or not self._pending_merges:
            return
        split = self._pending_merges.pop(0)
        hold_path(split.output)
        video = VideoInfo("", split.title, "", None, [], None)
        job = _ActiveJob(
            self._next_job,
            video,
            "",
            None,
            str(split.output.parent),
            None,
            split.resolution or "—",
        )
        self._next_job += 1
        job.state = "merging"
        job.split = split
        self._jobs[job.job_id] = job
        self._merging += 1
        self._mount_job(job)
        self._paint_job(job.job_id, "A juntar vídeo e áudio…", 0, None)
        threading.Thread(target=self._run_merge, args=(job,), daemon=True).start()

    def _run_merge(self, job: _ActiveJob) -> None:
        split = job.split
        assert split is not None

        def report(message: str, percent: float | None, rate: str | None = None) -> None:
            self.after(0, lambda m=message, p=percent, s=rate: self._paint_job(job.job_id, m, p, s))

        try:
            result: Path | BaseException | None = merge_streams(
                split.video, split.audio, split.output, report, job.pause_event
            )
        except MergeStopped:
            result = None
        except Exception as exc:  # noqa: BLE001 - mostrado na linha da junção
            result = exc
        finally:
            release_path(split.output)
        self.after(0, lambda finished=result: self._merge_finished(job.job_id, finished))

    def _merge_finished(self, job_id: int, result: Path | BaseException | None) -> None:
        self._merging = max(0, self._merging - 1)
        job = self._jobs.get(job_id)
        if result is None or self._closing:
            if job is not None and job.row is not None:
                self._dismiss_job(job_id)
            self._start_next_merge()
            return
        if isinstance(result, BaseException):
            text = str(result) if isinstance(result, DownloadError) else f"Falhou: {result}"
            if job is not None and job.detail is not None and job.progress is not None and job.percent is not None:
                job.state = "error"
                job.progress.stop()
                job.progress.configure(mode="determinate")
                job.progress["value"] = 0
                job.percent.configure(text="!")
                job.detail.configure(text=text, fg=ACCENT)
                self._sync_action(job)
            if self.winfo_exists():
                self.status_var.set(text)
                if not self._closing:
                    messagebox.showerror("Não foi possível juntar", text)
            self._start_next_merge()
            return
        if job is not None and job.split is not None:
            record_download(
                title=job.split.title,
                path=result,
                video_id="",
                thumbnail=None,
                resolution=job.split.resolution,
                url="",
            )
            self._refresh_history()
            self.status_var.set(f"Guardado em {result}")
            self._dismiss_job(job_id)
        self._start_next_merge()

    def _on_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._queue.clear()
        for job in list(self._jobs.values()):
            if job.state == "running":
                job.state = "pausing"
                job.pause_event.set()
            elif job.state == "merging":
                job.pause_event.set()
            elif job.state == "queued":
                job.state = "paused"
        deadline = time.monotonic() + 8
        while (self._running or self._merging) and time.monotonic() < deadline:
            self.update()
            time.sleep(0.05)
        for job in self._jobs.values():
            if job.state not in ("error", "merging"):
                job.state = "paused"
        self._save_active()
        self.destroy()

    def _job_finished(self, job_id: int, result: SavedDownload | BaseException) -> None:
        self._running = max(0, self._running - 1)
        job = self._jobs.get(job_id)
        if isinstance(result, DownloadPaused):
            if job is not None:
                job.outtmpl = result.template or job.outtmpl
                job.state = "paused"
                job.pause_event.clear()
                if self.winfo_exists():
                    self._show_paused(job)
            self._save_active()
            if not self._closing:
                self._start_ready_jobs()
            return
        if isinstance(result, BaseException):
            text = str(result) if isinstance(result, DownloadError) else f"Falhou: {result}"
            if job is not None and job.detail is not None and job.progress is not None and job.percent is not None:
                job.state = "error"
                job.progress.stop()
                job.progress.configure(mode="determinate")
                job.progress["value"] = 0
                job.percent.configure(text="!")
                job.detail.configure(text=text, fg=ACCENT)
                self._sync_action(job)
            if self.winfo_exists():
                self.status_var.set(text)
        elif job is not None:
            record_download(
                title=job.video.title,
                path=result.path,
                video_id=job.video.video_id,
                thumbnail=job.video.thumbnail,
                resolution=result.resolution or job.resolution,
                url=result.url,
            )
            self._refresh_history()
            self.status_var.set(f"Guardado em {result.path}")
            self._dismiss_job(job_id)
        if self._closing:
            self._save_active()
            return
        self._start_ready_jobs()
        if isinstance(result, BaseException):
            messagebox.showerror("Não foi possível descarregar", text)

    def _dismiss_job(self, job_id: int) -> None:
        job = self._jobs.pop(job_id, None)
        self._queue = [item for item in self._queue if item.job_id != job_id]
        if job is not None and job.state in ("paused", "error", "pausing"):
            discard_partials(job.outtmpl, job.video.title)
        if job is not None and job.row is not None:
            job.row.destroy()
        if not self._jobs:
            self._show_active_empty()
        self._canvas_fit(self.active_canvas)
        self._save_active()

    def _show_thumbnail(self, data: bytes | None) -> None:
        if not data:
            self._thumb_image = None
            self.thumb_label.configure(image="")
            return
        try:
            image = Image.open(BytesIO(data))
            image.thumbnail((240, 135), Image.Resampling.LANCZOS)
            self._thumb_image = ImageTk.PhotoImage(image)
        except Exception:
            self._thumb_image = None
            self.thumb_label.configure(image="")
            return
        self.thumb_label.configure(image=self._thumb_image)

    def _show_error(self, exc: BaseException) -> None:
        if isinstance(exc, DownloadError):
            text = str(exc)
        else:
            text = f"Falhou: {exc}"
        self.status_var.set(text)
        messagebox.showerror("Não foi possível descarregar", text)


def main() -> None:
    report = os.environ.get("YTDL_SELFTEST")
    if report:
        out = Path(report)
        try:
            from downloader import ensure_ffmpeg, find_node, probe

            info = probe("https://www.youtube.com/watch?v=RBmwAZdnmVY")
            out.write_text(
                "\n".join(
                    [
                        "OK",
                        find_node(),
                        ensure_ffmpeg(),
                        info.title,
                        str(len(info.resolutions)),
                    ]
                ),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001 - relatório de arranque
            out.write_text(f"FAIL\n{exc}\n", encoding="utf-8")
        return

    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
