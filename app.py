"""Programa Windows para descarregar vídeos do YouTube com o yt-dlp."""

from __future__ import annotations

import os
import threading
import tkinter as tk
from datetime import datetime
from io import BytesIO
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from downloader import (
    DownloadError,
    Resolution,
    SavedDownload,
    VideoInfo,
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
    load_browser,
    load_history,
    load_output_dir,
    record_download,
    save_browser,
    save_output_dir,
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


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("YouTube Downloader")
        self.geometry("1120x760")
        self.minsize(980, 680)
        self.configure(bg=BG)

        self.video: VideoInfo | None = None
        self.resolutions: list[Resolution] = []
        self.busy = False
        self._thumb_image: ImageTk.PhotoImage | None = None
        self._history_images: list[ImageTk.PhotoImage] = []

        self._build()
        self.output_var.set(load_output_dir() or str(Path.home() / "Downloads" / "YouTube"))
        self.output_var.trace_add("write", self._persist_output_dir)
        self._refresh_history()
        threading.Thread(target=self._import_previous_download, daemon=True).start()

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
        self._build_history(shell)

        ttk.Label(root, text="YouTube Downloader", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            root,
            text="Cola o link, escolhe a resolução e acompanha o progresso. O nome do ficheiro inclui a resolução, por isso o mesmo vídeo pode ficar guardado outra vez.",
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

        progress_card = ttk.Frame(root, style="Card.TFrame", padding=14)
        progress_card.grid(row=6, column=0, sticky="ew", pady=(16, 0))
        progress_card.columnconfigure(0, weight=1)

        header = ttk.Frame(progress_card, style="Card.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Progresso", style="Card.TLabel", font=("Segoe UI", 10, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        self.percent_var = tk.StringVar(value="0%")
        ttk.Label(header, textvariable=self.percent_var, style="Card.TLabel", font=("Segoe UI", 16, "bold")).grid(
            row=0, column=1, sticky="e"
        )

        self.progress = ttk.Progressbar(
            progress_card,
            style="red.Horizontal.TProgressbar",
            mode="determinate",
            maximum=100,
        )
        self.progress.grid(row=1, column=0, sticky="ew", pady=(10, 8), ipady=3)

        self.status_var = tk.StringVar(value="Pronto. Cola um link e carrega em Analisar.")
        ttk.Label(progress_card, textvariable=self.status_var, style="Card.TLabel", foreground=MUTED, wraplength=680).grid(
            row=2, column=0, sticky="w"
        )

        actions = ttk.Frame(root)
        actions.grid(row=7, column=0, sticky="ew", pady=(16, 0))
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

        ttk.Label(
            root,
            text="O áudio é juntado ao vídeo. Motor: yt-dlp.",
            style="Muted.TLabel",
        ).grid(row=8, column=0, sticky="w", pady=(18, 0))

    def _build_history(self, shell: ttk.Frame) -> None:
        side = ttk.Frame(shell, style="Card.TFrame", padding=12)
        side.grid(row=0, column=1, sticky="nsew")
        side.rowconfigure(1, weight=1)
        side.columnconfigure(0, weight=1)

        ttk.Label(side, text="Últimos downloads", style="Card.TLabel", font=("Segoe UI", 12, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 10)
        )
        self.history_canvas = tk.Canvas(side, bg=CARD, highlightthickness=0, width=280, bd=0)
        self.history_canvas.grid(row=1, column=0, sticky="nsew")
        self.history_inner = ttk.Frame(self.history_canvas, style="Card.TFrame")
        self.history_window = self.history_canvas.create_window((0, 0), window=self.history_inner, anchor="nw")
        self.history_inner.bind("<Configure>", self._history_resized)
        self.history_canvas.bind("<Configure>", self._history_canvas_resized)
        self.history_canvas.bind("<Enter>", lambda _event: self.history_canvas.bind_all("<MouseWheel>", self._history_wheel))
        self.history_canvas.bind("<Leave>", lambda _event: self.history_canvas.unbind_all("<MouseWheel>"))

    def _history_resized(self, _event) -> None:
        self.history_canvas.configure(scrollregion=self.history_canvas.bbox("all"))

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
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("Link em falta", "Cola o link do vídeo.")
            return
        self._show_thumbnail(None)
        browser = self._selected_browser()
        self._run_async(lambda: probe(url, browser), self._probe_done, "A analisar o vídeo…")

    def _probe_done(self, result: VideoInfo | BaseException) -> None:
        if isinstance(result, BaseException):
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
        self.percent_var.set("0%")
        self.status_var.set("Escolhe a resolução e carrega em Descarregar. O som vem incluído.")
        self.progress["value"] = 0
        self.download_button.configure(state="normal")

    def on_download(self) -> None:
        if self.video is None:
            messagebox.showwarning("Sem vídeo", "Analisa o link antes de descarregar.")
            return
        url = self.url_var.get().strip()
        folder = self.output_var.get().strip()
        if not folder:
            messagebox.showwarning("Pasta em falta", "Escolhe a pasta onde o vídeo vai ficar.")
            return
        height = self._selected_height()
        browser = self._selected_browser()

        def work() -> SavedDownload:
            title = self.video.title if self.video is not None else None
            return download(url, height, Path(folder), self._report_progress, browser, title)

        self._run_async(work, self._download_done, "A iniciar o download…")

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

    def _download_done(self, result: SavedDownload | BaseException) -> None:
        if isinstance(result, BaseException):
            self._show_error(result)
            return
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.progress["value"] = 100
        self.percent_var.set("100%")
        self.status_var.set(f"Guardado em {result.path}")
        if self.video is not None:
            record_download(
                title=self.video.title,
                path=result.path,
                video_id=self.video.video_id,
                thumbnail=self.video.thumbnail,
                resolution=result.resolution,
                url=result.url,
            )
            self._refresh_history()
        messagebox.showinfo("Download concluído", f"O vídeo com som ficou em:\n{result.path}")

    def _report_progress(self, message: str, percent: float | None) -> None:
        self.after(0, lambda: self._apply_progress(message, percent))

    def _apply_progress(self, message: str, percent: float | None) -> None:
        self.status_var.set(message)
        self.percent_var.set("…" if percent is None else f"{percent:.0f}%")
        if percent is None:
            if str(self.progress["mode"]) != "indeterminate":
                self.progress.configure(mode="indeterminate")
                self.progress.start(12)
            return
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.progress["value"] = percent

    def _run_async(self, work, done, status: str) -> None:
        if self.busy:
            return
        self.busy = True
        self.download_button.configure(state="disabled")
        self.probe_button.state(["disabled"])
        self.status_var.set(status)
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)

        def runner() -> None:
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001 - mostrado na janela
                result = exc
            self.after(0, lambda: self._finish(done, result))

        threading.Thread(target=runner, daemon=True).start()

    def _finish(self, done, result) -> None:
        self.busy = False
        self.probe_button.state(["!disabled"])
        self.download_button.configure(state="normal" if self.video is not None else "disabled")
        self.progress.stop()
        self.progress.configure(mode="determinate")
        done(result)

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
        self.progress["value"] = 0
        self.percent_var.set("0%")
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
