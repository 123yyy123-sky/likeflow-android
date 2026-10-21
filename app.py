from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk

from PIL import Image, ImageTk


APP_NAME = "LikeFlow"
APP_VERSION = "1.0.1"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
TOTAL_RE = re.compile(r"total=(\d+)/(\d+)")
RUN_DIR_RE = re.compile(r"^\d{8}_\d{6}$")

COLORS = {
    "canvas": "#F3EEE5",
    "panel": "#FFFCF7",
    "panel_alt": "#E9E2D6",
    "ink": "#172B2D",
    "muted": "#667471",
    "accent": "#0F766E",
    "accent_hover": "#0A5E58",
    "coral": "#E76F51",
    "coral_hover": "#C9573C",
    "line": "#D8D0C3",
    "sidebar": "#143C3A",
    "sidebar_ink": "#F8F1E7",
    "success": "#2B8A67",
    "warning": "#B96A25",
}


def resource_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


BASE_DIR = resource_root()
CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
CONFIG_PATH = CONFIG_DIR / "config.json"


def default_config() -> dict[str, object]:
    return {
        "serial": "",
        "adb_dir": r"F:\模拟器\LDPlayer14",
        "package_prefix": "com.tencent.mobileqq",
        "unliked_template": str(BASE_DIR / "templates" / "unliked_heart.png"),
        "liked_template": str(BASE_DIR / "templates" / "liked_heart.png"),
        "total_likes": 1450,
        "rounds": 5,
        "per_round": 100,
        "max_scrolls": 30,
        "fast": True,
        "dry_run": False,
        "artifacts_dir": str(CONFIG_DIR / "artifacts"),
    }


def load_config() -> dict[str, object]:
    config = default_config()
    try:
        saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(saved, dict):
            config.update(saved)
    except (OSError, ValueError):
        pass
    return config


def adb_executable(path_value: str) -> Path | None:
    value = path_value.strip()
    if value:
        path = Path(value)
        if path.is_file():
            return path
        candidate = path / ("adb.exe" if os.name == "nt" else "adb")
        if candidate.is_file():
            return candidate
    found = shutil.which("adb")
    return Path(found) if found else None


def directory_size(path: Path) -> int:
    total = 0
    try:
        for item in path.rglob("*"):
            if item.is_file():
                try:
                    total += item.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def cleanup_artifacts(root: Path) -> tuple[int, int, int]:
    """Delete generated batch directories, preserving unrelated files."""
    if not root.is_dir():
        return 0, 0, 0

    removed = 0
    skipped = 0
    freed_bytes = 0
    for entry in root.iterdir():
        if (
            entry.is_symlink()
            or not entry.is_dir()
            or not RUN_DIR_RE.fullmatch(entry.name)
        ):
            skipped += 1
            continue
        size = directory_size(entry)
        try:
            shutil.rmtree(entry)
        except OSError:
            skipped += 1
            continue
        removed += 1
        freed_bytes += size
    return removed, skipped, freed_bytes


class LikeFlowApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        config = load_config()

        self.title(f"LikeFlow {APP_VERSION}")
        icon_path = BASE_DIR / "assets" / "likeflow.ico"
        if icon_path.is_file():
            try:
                self.iconbitmap(default=str(icon_path))
            except tk.TclError:
                pass
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        window_width = min(1160, max(980, screen_width - 80))
        window_height = min(790, max(640, screen_height - 100))
        window_x = max(0, (screen_width - window_width) // 2)
        window_y = max(0, (screen_height - window_height) // 2)
        self.geometry(
            f"{window_width}x{window_height}+{window_x}+{window_y}"
        )
        self.minsize(980, 640)
        self.configure(bg=COLORS["canvas"])

        self.process: subprocess.Popen[str] | None = None
        self.reader_thread: threading.Thread | None = None
        self.unliked_photo: ImageTk.PhotoImage | None = None
        self.liked_photo: ImageTk.PhotoImage | None = None

        self.serial_var = tk.StringVar(value=str(config["serial"]))
        self.adb_var = tk.StringVar(value=str(config["adb_dir"]))
        self.package_var = tk.StringVar(value=str(config["package_prefix"]))
        self.unliked_var = tk.StringVar(value=str(config["unliked_template"]))
        self.liked_var = tk.StringVar(value=str(config["liked_template"]))
        self.total_var = tk.StringVar(value=str(config["total_likes"]))
        self.rounds_var = tk.StringVar(value=str(config["rounds"]))
        self.per_round_var = tk.StringVar(value=str(config["per_round"]))
        self.scrolls_var = tk.StringVar(value=str(config["max_scrolls"]))
        self.fast_var = tk.BooleanVar(value=bool(config["fast"]))
        self.dry_run_var = tk.BooleanVar(value=bool(config["dry_run"]))
        self.artifacts_var = tk.StringVar(value=str(config["artifacts_dir"]))
        self.status_var = tk.StringVar(value="等待开始")
        self.progress_var = tk.StringVar(value="0 / 0")

        self._configure_styles()
        self._build_layout()
        self._load_previews()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(250, self.refresh_devices)

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "App.Horizontal.TProgressbar",
            troughcolor=COLORS["panel_alt"],
            background=COLORS["accent"],
            bordercolor=COLORS["panel_alt"],
            lightcolor=COLORS["accent"],
            darkcolor=COLORS["accent"],
            thickness=10,
        )
        style.configure(
            "App.TCombobox",
            fieldbackground=COLORS["panel"],
            background=COLORS["panel"],
            foreground=COLORS["ink"],
            bordercolor=COLORS["line"],
            arrowsize=15,
            padding=7,
        )
        style.map(
            "App.TCombobox",
            fieldbackground=[("readonly", COLORS["panel"])],
            selectbackground=[("readonly", COLORS["accent"])],
            selectforeground=[("readonly", "white")],
        )

    def _build_layout(self) -> None:
        shell = tk.Frame(self, bg=COLORS["canvas"])
        shell.pack(fill="both", expand=True)

        sidebar = tk.Frame(shell, bg=COLORS["sidebar"], width=238)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        self._build_sidebar(sidebar)

        main = tk.Frame(shell, bg=COLORS["canvas"])
        main.pack(side="left", fill="both", expand=True, padx=26, pady=22)
        self._build_header(main)
        self._build_configuration(main)
        self._build_actions(main)
        self._build_log(main)

    def _build_sidebar(self, parent: tk.Frame) -> None:
        tk.Label(
            parent,
            text="LIKE\nFLOW",
            justify="left",
            font=("Georgia", 30, "bold"),
            fg=COLORS["sidebar_ink"],
            bg=COLORS["sidebar"],
        ).pack(anchor="w", padx=28, pady=(34, 4))
        tk.Label(
            parent,
            text="ANDROID AUTOMATION DESK",
            font=("Consolas", 8, "bold"),
            fg="#9BC8BD",
            bg=COLORS["sidebar"],
        ).pack(anchor="w", padx=30)

        tk.Frame(parent, bg="#37615D", height=1).pack(
            fill="x", padx=28, pady=28
        )
        for number, label in (
            ("01", "连接设备"),
            ("02", "设置图标"),
            ("03", "设定数量"),
            ("04", "运行与查看"),
        ):
            row = tk.Frame(parent, bg=COLORS["sidebar"])
            row.pack(fill="x", padx=28, pady=8)
            tk.Label(
                row,
                text=number,
                font=("Consolas", 9, "bold"),
                fg="#7FB3A8",
                bg=COLORS["sidebar"],
                width=3,
                anchor="w",
            ).pack(side="left")
            tk.Label(
                row,
                text=label,
                font=("Microsoft YaHei UI", 10),
                fg=COLORS["sidebar_ink"],
                bg=COLORS["sidebar"],
            ).pack(side="left")

        footer = tk.Frame(parent, bg="#0D302E")
        footer.pack(side="bottom", fill="x", padx=18, pady=18)
        tk.Label(
            footer,
            text="本地运行 · 不上传账号数据",
            font=("Microsoft YaHei UI", 9),
            fg="#B2D5CD",
            bg="#0D302E",
        ).pack(anchor="w", padx=12, pady=(10, 3))
        tk.Label(
            footer,
            text=f"v{APP_VERSION}",
            font=("Consolas", 8),
            fg="#6FA197",
            bg="#0D302E",
        ).pack(anchor="w", padx=12, pady=(0, 10))

    def _build_header(self, parent: tk.Frame) -> None:
        header = tk.Frame(parent, bg=COLORS["canvas"])
        header.pack(fill="x")
        left = tk.Frame(header, bg=COLORS["canvas"])
        left.pack(side="left")
        tk.Label(
            left,
            text="运行控制台",
            font=("Microsoft YaHei UI", 23, "bold"),
            fg=COLORS["ink"],
            bg=COLORS["canvas"],
        ).pack(anchor="w")
        tk.Label(
            left,
            text="设置目标数量与图型，然后启动一轮可监控的自动任务。",
            font=("Microsoft YaHei UI", 10),
            fg=COLORS["muted"],
            bg=COLORS["canvas"],
        ).pack(anchor="w", pady=(3, 0))

        status = tk.Frame(header, bg=COLORS["panel"], padx=14, pady=8)
        status.pack(side="right", anchor="n")
        self.status_dot = tk.Canvas(
            status,
            width=10,
            height=10,
            bg=COLORS["panel"],
            highlightthickness=0,
        )
        self.status_dot.pack(side="left", padx=(0, 8))
        self.status_dot.create_oval(
            1, 1, 9, 9, fill=COLORS["warning"], outline=""
        )
        tk.Label(
            status,
            textvariable=self.status_var,
            font=("Microsoft YaHei UI", 9, "bold"),
            fg=COLORS["ink"],
            bg=COLORS["panel"],
        ).pack(side="left")

    def _card(self, parent: tk.Widget) -> tk.Frame:
        card = tk.Frame(
            parent,
            bg=COLORS["panel"],
            highlightbackground=COLORS["line"],
            highlightthickness=1,
        )
        return card

    def _section_title(
        self, parent: tk.Widget, marker: str, title: str, subtitle: str
    ) -> None:
        row = tk.Frame(parent, bg=COLORS["panel"])
        row.pack(fill="x", anchor="w")
        tk.Label(
            row,
            text=marker,
            font=("Consolas", 9, "bold"),
            fg=COLORS["accent"],
            bg=COLORS["panel"],
        ).pack(side="left", padx=(0, 9))
        tk.Label(
            row,
            text=title,
            font=("Microsoft YaHei UI", 11, "bold"),
            fg=COLORS["ink"],
            bg=COLORS["panel"],
        ).pack(side="left")
        tk.Label(
            row,
            text=subtitle,
            font=("Microsoft YaHei UI", 8),
            fg=COLORS["muted"],
            bg=COLORS["panel"],
        ).pack(side="left", padx=(10, 0))

    def _entry(
        self, parent: tk.Widget, variable: tk.StringVar, width: int = 30
    ) -> tk.Entry:
        return tk.Entry(
            parent,
            textvariable=variable,
            width=width,
            font=("Microsoft YaHei UI", 9),
            fg=COLORS["ink"],
            bg="#FFFFFF",
            insertbackground=COLORS["ink"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=COLORS["line"],
            highlightcolor=COLORS["accent"],
        )

    def _button(
        self,
        parent: tk.Widget,
        text: str,
        command,
        *,
        primary: bool = False,
        danger: bool = False,
        width: int = 12,
    ) -> tk.Button:
        bg = COLORS["accent"] if primary else COLORS["panel"]
        fg = "white" if primary else COLORS["ink"]
        active = COLORS["accent_hover"] if primary else COLORS["panel_alt"]
        if danger:
            bg = COLORS["coral"]
            fg = "white"
            active = COLORS["coral_hover"]
        return tk.Button(
            parent,
            text=text,
            command=command,
            width=width,
            font=("Microsoft YaHei UI", 9, "bold"),
            fg=fg,
            bg=bg,
            activebackground=active,
            activeforeground=fg,
            relief="flat",
            bd=0,
            padx=12,
            pady=8,
            cursor="hand2",
            highlightthickness=1 if not primary and not danger else 0,
            highlightbackground=COLORS["line"],
        )

    def _build_configuration(self, parent: tk.Frame) -> None:
        card = self._card(parent)
        card.pack(fill="x", pady=(16, 12))
        self._build_device_section(card)
        tk.Frame(card, bg=COLORS["line"], height=1).pack(
            fill="x", padx=20, pady=(0, 13)
        )
        self._build_template_section(card)

    def _build_device_section(self, card: tk.Frame) -> None:
        body = tk.Frame(card, bg=COLORS["panel"])
        body.pack(fill="x", padx=20, pady=(15, 12))
        self._section_title(body, "01", "设备与目标", "ADB CONNECTION")

        form = tk.Frame(body, bg=COLORS["panel"])
        form.pack(fill="x", pady=(11, 0))
        form.columnconfigure(0, weight=1)
        form.columnconfigure(1, weight=1)

        device_field = tk.Frame(form, bg=COLORS["panel"])
        device_field.grid(row=0, column=0, sticky="ew", padx=(0, 9))
        tk.Label(
            device_field,
            text="设备",
            font=("Microsoft YaHei UI", 8),
            fg=COLORS["muted"],
            bg=COLORS["panel"],
        ).pack(anchor="w")
        device_row = tk.Frame(device_field, bg=COLORS["panel"])
        device_row.pack(fill="x", pady=(3, 0))
        self.serial_combo = ttk.Combobox(
            device_row,
            textvariable=self.serial_var,
            style="App.TCombobox",
            width=18,
        )
        self.serial_combo.pack(side="left", fill="x", expand=True)
        self._button(
            device_row, "刷新", self.refresh_devices, width=5
        ).pack(side="left", padx=(7, 0))

        package_field = tk.Frame(form, bg=COLORS["panel"])
        package_field.grid(row=0, column=1, sticky="ew", padx=(9, 0))
        tk.Label(
            package_field,
            text="包名",
            font=("Microsoft YaHei UI", 8),
            fg=COLORS["muted"],
            bg=COLORS["panel"],
        ).pack(anchor="w")
        self._entry(package_field, self.package_var, width=22).pack(
            fill="x", ipady=5, pady=(3, 0)
        )

        adb_field = tk.Frame(form, bg=COLORS["panel"])
        adb_field.grid(row=1, column=0, sticky="ew", padx=(0, 9), pady=(9, 0))
        tk.Label(
            adb_field,
            text="ADB 目录",
            font=("Microsoft YaHei UI", 8),
            fg=COLORS["muted"],
            bg=COLORS["panel"],
        ).pack(anchor="w")
        adb_row = tk.Frame(adb_field, bg=COLORS["panel"])
        adb_row.pack(fill="x", pady=(3, 0))
        self._entry(adb_row, self.adb_var, width=18).pack(
            side="left", fill="x", expand=True, ipady=5
        )
        self._button(
            adb_row,
            "选择",
            lambda: self._browse_directory(self.adb_var),
            width=5,
        ).pack(side="left", padx=(7, 0))

        total_field = tk.Frame(form, bg=COLORS["panel"])
        total_field.grid(row=1, column=1, sticky="ew", padx=(9, 0), pady=(9, 0))
        tk.Label(
            total_field,
            text="点赞总数",
            font=("Microsoft YaHei UI", 8),
            fg=COLORS["muted"],
            bg=COLORS["panel"],
        ).pack(anchor="w")
        total_row = tk.Frame(total_field, bg=COLORS["panel"])
        total_row.pack(fill="x", pady=(3, 0))
        self._entry(total_row, self.total_var, width=10).pack(
            side="left", ipady=5
        )
        tk.Label(
            total_row,
            text="累计达到该数量后停止",
            font=("Microsoft YaHei UI", 8),
            fg=COLORS["muted"],
            bg=COLORS["panel"],
        ).pack(side="left", padx=(9, 0))

    def _build_template_section(self, card: tk.Frame) -> None:
        body = tk.Frame(card, bg=COLORS["panel"])
        body.pack(fill="x", padx=20, pady=(0, 15))
        self._section_title(body, "02", "点赞图型", "IMAGE TEMPLATES")

        template_row = tk.Frame(body, bg=COLORS["panel"])
        template_row.pack(fill="x", pady=(11, 0))

        previews = tk.Frame(template_row, bg=COLORS["panel"])
        previews.pack(side="left")

        left = tk.Frame(previews, bg=COLORS["panel_alt"], padx=8, pady=7)
        left.pack(side="left", padx=(0, 6))
        tk.Label(
            left,
            text="未点赞 · 必选",
            font=("Microsoft YaHei UI", 7, "bold"),
            fg=COLORS["muted"],
            bg=COLORS["panel_alt"],
        ).pack()
        self.unliked_preview = tk.Label(
            left,
            text="PNG / JPG",
            width=9,
            height=3,
            font=("Microsoft YaHei UI", 7),
            fg=COLORS["muted"],
            bg=COLORS["panel_alt"],
        )
        self.unliked_preview.pack(pady=3)
        self._button(
            left,
            "选择",
            lambda: self._browse_image(
                self.unliked_var, self.unliked_preview, "unliked"
            ),
            width=5,
        ).pack(fill="x")

        right = tk.Frame(previews, bg=COLORS["panel_alt"], padx=8, pady=7)
        right.pack(side="left")
        tk.Label(
            right,
            text="已点赞 · 可选",
            font=("Microsoft YaHei UI", 7, "bold"),
            fg=COLORS["muted"],
            bg=COLORS["panel_alt"],
        ).pack()
        self.liked_preview = tk.Label(
            right,
            text="状态验证",
            width=9,
            height=3,
            font=("Microsoft YaHei UI", 7),
            fg=COLORS["muted"],
            bg=COLORS["panel_alt"],
        )
        self.liked_preview.pack(pady=3)
        self._button(
            right,
            "选择",
            lambda: self._browse_image(
                self.liked_var, self.liked_preview, "liked"
            ),
            width=5,
        ).pack(fill="x")

        params = tk.Frame(template_row, bg=COLORS["panel"])
        params.pack(side="left", fill="x", expand=True, padx=(16, 0))
        for column in range(4):
            params.columnconfigure(column, weight=1)
        self._param_field(params, 0, "每次轮次", self.rounds_var)
        self._param_field(params, 1, "每轮上限", self.per_round_var)
        self._param_field(params, 2, "滑屏上限", self.scrolls_var)
        tk.Label(
            params,
            text="运行模式",
            font=("Microsoft YaHei UI", 8),
            fg=COLORS["muted"],
            bg=COLORS["panel"],
        ).grid(row=0, column=3, sticky="w", padx=(5, 0))

        mode = tk.Frame(params, bg=COLORS["panel"])
        mode.grid(row=1, column=3, sticky="w", padx=(5, 0), pady=(2, 0))
        tk.Checkbutton(
            mode,
            text="快速",
            variable=self.fast_var,
            font=("Microsoft YaHei UI", 8),
            fg=COLORS["ink"],
            bg=COLORS["panel"],
            activebackground=COLORS["panel"],
            selectcolor=COLORS["panel"],
            cursor="hand2",
        ).pack(side="left")
        tk.Checkbutton(
            mode,
            text="仅检测",
            variable=self.dry_run_var,
            font=("Microsoft YaHei UI", 8),
            fg=COLORS["ink"],
            bg=COLORS["panel"],
            activebackground=COLORS["panel"],
            selectcolor=COLORS["panel"],
            cursor="hand2",
        ).pack(side="left", padx=(5, 0))

    def _param_field(
        self,
        parent: tk.Widget,
        column: int,
        label: str,
        variable: tk.StringVar,
    ) -> None:
        frame = tk.Frame(parent, bg=COLORS["panel"])
        frame.grid(
            row=0,
            column=column,
            rowspan=2,
            sticky="ew",
            padx=(0 if column == 0 else 5, 0),
        )
        tk.Label(
            frame,
            text=label,
            font=("Microsoft YaHei UI", 8),
            fg=COLORS["muted"],
            bg=COLORS["panel"],
        ).pack(anchor="w")
        entry = self._entry(frame, variable, width=7)
        entry.pack(fill="x", ipady=4, pady=(2, 0))

    def _build_actions(self, parent: tk.Frame) -> None:
        actions = tk.Frame(parent, bg=COLORS["canvas"])
        actions.pack(fill="x", pady=(0, 12))

        self.start_button = self._button(
            actions, "开始执行", self.start_run, primary=True, width=14
        )
        self.start_button.pack(side="left")
        self.stop_button = self._button(
            actions,
            "停止任务",
            self.stop_run,
            danger=True,
            width=12,
        )
        self.stop_button.pack(side="left", padx=(9, 0))
        self.stop_button.configure(state="disabled")
        self._button(
            actions,
            "打开输出目录",
            self.open_artifacts,
            width=12,
        ).pack(side="left", padx=(9, 0))
        self.clean_button = self._button(
            actions,
            "清理缓存",
            self.clean_artifacts,
            width=10,
        )
        self.clean_button.pack(side="left", padx=(9, 0))

        progress_group = tk.Frame(actions, bg=COLORS["canvas"])
        progress_group.pack(side="right", fill="x", expand=True, padx=(24, 0))
        progress_head = tk.Frame(progress_group, bg=COLORS["canvas"])
        progress_head.pack(fill="x")
        tk.Label(
            progress_head,
            text="任务进度",
            font=("Microsoft YaHei UI", 8, "bold"),
            fg=COLORS["muted"],
            bg=COLORS["canvas"],
        ).pack(side="left")
        tk.Label(
            progress_head,
            textvariable=self.progress_var,
            font=("Consolas", 8, "bold"),
            fg=COLORS["accent"],
            bg=COLORS["canvas"],
        ).pack(side="right")
        self.progress = ttk.Progressbar(
            progress_group,
            style="App.Horizontal.TProgressbar",
            mode="determinate",
            maximum=1,
            value=0,
        )
        self.progress.pack(fill="x", pady=(5, 0))

    def _build_log(self, parent: tk.Frame) -> None:
        card = self._card(parent)
        card.pack(fill="both", expand=True)
        body = tk.Frame(card, bg=COLORS["panel"])
        body.pack(fill="both", expand=True, padx=16, pady=12)

        head = tk.Frame(body, bg=COLORS["panel"])
        head.pack(fill="x", pady=(0, 7))
        tk.Label(
            head,
            text="实时日志",
            font=("Microsoft YaHei UI", 10, "bold"),
            fg=COLORS["ink"],
            bg=COLORS["panel"],
        ).pack(side="left")
        tk.Label(
            head,
            text="批次目标、检测和错误信息会显示在这里",
            font=("Microsoft YaHei UI", 8),
            fg=COLORS["muted"],
            bg=COLORS["panel"],
        ).pack(side="left", padx=10)
        self._button(head, "清空", self.clear_log, width=6).pack(side="right")

        text_frame = tk.Frame(body, bg="#102624")
        text_frame.pack(fill="both", expand=True)
        self.log_text = tk.Text(
            text_frame,
            wrap="word",
            font=("Consolas", 9),
            fg="#DCEAE5",
            bg="#102624",
            insertbackground="#DCEAE5",
            relief="flat",
            padx=12,
            pady=10,
            state="disabled",
        )
        scrollbar = ttk.Scrollbar(
            text_frame, orient="vertical", command=self.log_text.yview
        )
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def _set_preview(self, label: tk.Label, path_value: str, kind: str) -> None:
        path = Path(path_value)
        if not path.is_file():
            label.configure(text="未选择文件", image="")
            return
        try:
            image = Image.open(path).convert("RGB")
            image.thumbnail((64, 64), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(image)
            label.configure(image=photo, text="", width=64, height=64)
            if kind == "unliked":
                self.unliked_photo = photo
            else:
                self.liked_photo = photo
        except OSError:
            label.configure(text="无法预览", image="")

    def _load_previews(self) -> None:
        self._set_preview(
            self.unliked_preview, self.unliked_var.get(), "unliked"
        )
        self._set_preview(self.liked_preview, self.liked_var.get(), "liked")

    def _browse_image(
        self, variable: tk.StringVar, label: tk.Label, kind: str
    ) -> None:
        path = filedialog.askopenfilename(
            title="选择点赞图型",
            filetypes=(
                ("图片文件", "*.png *.jpg *.jpeg *.bmp *.webp"),
                ("所有文件", "*.*"),
            ),
        )
        if path:
            variable.set(path)
            self._set_preview(label, path, kind)

    def _browse_directory(self, variable: tk.StringVar) -> None:
        path = filedialog.askdirectory(title="选择 ADB 所在目录")
        if path:
            variable.set(path)
            self.refresh_devices()

    def refresh_devices(self) -> None:
        adb = adb_executable(self.adb_var.get())
        if adb is None:
            self._set_status("未找到 adb", COLORS["coral"])
            return
        try:
            completed = subprocess.run(
                [str(adb), "devices"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
                creationflags=CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._set_status("设备刷新失败", COLORS["coral"])
            self._append_log(f"刷新设备失败：{exc}\n")
            return

        devices = []
        for line in completed.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                devices.append(parts[0])
        self.serial_combo.configure(values=devices)
        if self.serial_var.get() not in devices and devices:
            self.serial_var.set(devices[0])
        if devices:
            self._set_status(f"已连接 {len(devices)} 台设备", COLORS["success"])
        else:
            self._set_status("未检测到设备", COLORS["warning"])

    def _read_process_output(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            self.after(0, self._handle_output_line, line.rstrip())
        return_code = process.wait()
        self.after(0, self._finish_run, return_code)

    def _handle_output_line(self, line: str) -> None:
        self._append_log(line + "\n")
        match = TOTAL_RE.search(line)
        if match:
            current = int(match.group(1))
            target = max(1, int(match.group(2)))
            self.progress.configure(maximum=target, value=min(current, target))
            self.progress_var.set(f"{current} / {target}")

    def _started(self, command: list[str]) -> None:
        self.process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=CREATE_NO_WINDOW,
        )
        self.reader_thread = threading.Thread(
            target=self._read_process_output, daemon=True
        )
        self.reader_thread.start()

    def start_run(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        try:
            total = self._positive_int(self.total_var.get(), "点赞总数")
            rounds = self._positive_int(self.rounds_var.get(), "每次轮次")
            per_round = self._positive_int(self.per_round_var.get(), "每轮上限")
            scrolls = self._positive_int(self.scrolls_var.get(), "滑屏上限")
        except ValueError as exc:
            messagebox.showerror("参数错误", str(exc), parent=self)
            return

        if per_round > 100:
            messagebox.showerror("参数错误", "每轮上限不能超过 100。", parent=self)
            return
        if scrolls > 50:
            messagebox.showerror("参数错误", "滑屏上限不能超过 50。", parent=self)
            return

        template = Path(self.unliked_var.get())
        liked = Path(self.liked_var.get()) if self.liked_var.get().strip() else None
        if not template.is_file():
            messagebox.showerror("图型错误", "请选择有效的未点赞图型。", parent=self)
            return
        if liked is not None and not liked.is_file():
            messagebox.showerror("图型错误", "已点赞图型路径不存在。", parent=self)
            return
        if adb_executable(self.adb_var.get()) is None:
            messagebox.showerror("ADB 错误", "未找到 adb，请选择正确目录。", parent=self)
            return

        artifacts = Path(self.artifacts_var.get())
        artifacts.mkdir(parents=True, exist_ok=True)
        self._save_config()

        worker_args = [
            "--serial",
            self.serial_var.get().strip(),
            "--adb-dir",
            self.adb_var.get().strip(),
            "--package-prefix",
            self.package_var.get().strip(),
            "--template",
            str(template.resolve()),
            "--rounds",
            str(rounds),
            "--per-round",
            str(per_round),
            "--max-scrolls-per-round",
            str(scrolls),
            "--min-total-clicks",
            str(total),
            "--artifacts-dir",
            str(artifacts.resolve()),
        ]
        if not worker_args[1]:
            worker_args = worker_args[2:]
        if liked is not None:
            worker_args.extend(["--liked-template", str(liked.resolve())])
        if self.fast_var.get():
            worker_args.append("--fast")
        if self.dry_run_var.get():
            worker_args.append("--dry-run")

        if getattr(sys, "frozen", False):
            command = [sys.executable, "--run-batch", *worker_args]
        else:
            command = [
                sys.executable,
                "-u",
                str(Path(__file__).resolve()),
                "--run-batch",
                *worker_args,
            ]
        self.clear_log()
        self.progress.configure(maximum=max(1, total), value=0)
        self.progress_var.set(f"0 / {total}")
        self._set_running(True)
        action = "检测" if self.dry_run_var.get() else "执行"
        self._set_status(f"正在{action}", COLORS["accent"])
        self._append_log(
            f"启动任务：目标 {total} 次，每轮上限 {per_round}，"
            f"{'快速模式' if self.fast_var.get() else '标准模式'}\n"
        )
        self._started(command)

    def stop_run(self) -> None:
        process = self.process
        if process is None or process.poll() is not None:
            return
        self._set_status("正在停止", COLORS["warning"])
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                    creationflags=CREATE_NO_WINDOW,
                )
            else:
                process.terminate()
        except OSError as exc:
            self._append_log(f"停止任务失败：{exc}\n")

    def _finish_run(self, return_code: int) -> None:
        self._set_running(False)
        if return_code == 0:
            self._set_status("任务完成", COLORS["success"])
            self._append_log("\n任务完成。\n")
        elif return_code == 3:
            self._set_status("未达到目标", COLORS["coral"])
            self._append_log("\n任务结束，但未达到设定数量。\n")
        else:
            self._set_status("任务已停止", COLORS["coral"])
            self._append_log(f"\n任务退出，代码：{return_code}\n")
        self.process = None

    def _set_running(self, running: bool) -> None:
        self.start_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")
        self.clean_button.configure(state="disabled" if running else "normal")

    def _set_status(self, text: str, color: str) -> None:
        self.status_var.set(text)
        self.status_dot.delete("all")
        self.status_dot.create_oval(1, 1, 9, 9, fill=color, outline="")

    def _append_log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def open_artifacts(self) -> None:
        path = Path(self.artifacts_var.get())
        path.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(path)
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def clean_artifacts(self) -> None:
        if self.process is not None and self.process.poll() is None:
            messagebox.showwarning(
                "任务运行中",
                "请先停止任务，再清理缓存。",
                parent=self,
            )
            return

        root = Path(self.artifacts_var.get()).expanduser()
        run_dirs = (
            [
                entry
                for entry in root.iterdir()
                if not entry.is_symlink()
                and entry.is_dir()
                and RUN_DIR_RE.fullmatch(entry.name)
            ]
            if root.is_dir()
            else []
        )
        if not run_dirs:
            messagebox.showinfo(
                "清理缓存",
                "没有可清理的运行缓存。",
                parent=self,
            )
            return

        total_size = sum(directory_size(path) for path in run_dirs)
        if not messagebox.askyesno(
            "清理缓存",
            f"将删除 {len(run_dirs)} 个运行缓存目录，"
            f"预计释放 {self._format_size(total_size)}。\n\n"
            "配置文件和点赞图型不会被删除。是否继续？",
            parent=self,
        ):
            return

        removed, skipped, freed_bytes = cleanup_artifacts(root)
        if removed:
            self._set_status("缓存已清理", COLORS["success"])
            self._append_log(
                f"已清理 {removed} 个运行缓存目录，"
                f"释放 {self._format_size(freed_bytes)}。\n"
            )
        else:
            self._set_status("清理失败", COLORS["coral"])
        if skipped:
            self._append_log(f"保留了 {skipped} 个非运行缓存项目。\n")
        if removed:
            messagebox.showinfo(
                "清理完成",
                f"已清理 {removed} 个运行缓存目录，"
                f"释放 {self._format_size(freed_bytes)}。",
                parent=self,
            )
        else:
            messagebox.showwarning(
                "清理失败",
                "缓存文件可能正在被其他程序使用，请关闭后重试。",
                parent=self,
            )

    @staticmethod
    def _format_size(size: int) -> str:
        value = float(size)
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
            value /= 1024
        return f"{value:.1f} GB"

    def _positive_int(self, value: str, label: str) -> int:
        try:
            number = int(value)
        except ValueError as exc:
            raise ValueError(f"{label}必须是整数。") from exc
        if number < 1:
            raise ValueError(f"{label}必须大于 0。")
        return number

    def _save_config(self) -> None:
        config = {
            "serial": self.serial_var.get(),
            "adb_dir": self.adb_var.get(),
            "package_prefix": self.package_var.get(),
            "unliked_template": self.unliked_var.get(),
            "liked_template": self.liked_var.get(),
            "total_likes": self.total_var.get(),
            "rounds": self.rounds_var.get(),
            "per_round": self.per_round_var.get(),
            "max_scrolls": self.scrolls_var.get(),
            "fast": self.fast_var.get(),
            "dry_run": self.dry_run_var.get(),
            "artifacts_dir": self.artifacts_var.get(),
        }
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            CONFIG_PATH.write_text(
                json.dumps(config, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            self._append_log(f"保存配置失败：{exc}\n")

    def _on_close(self) -> None:
        if self.process is not None and self.process.poll() is None:
            if not messagebox.askyesno(
                "退出 LikeFlow",
                "任务仍在运行，是否停止任务并退出？",
                parent=self,
            ):
                return
            self.stop_run()
        self._save_config()
        self.destroy()


def run_batch_worker(arguments: list[str]) -> int:
    import batch_test

    sys.argv = ["batch_test.py", *arguments]
    return batch_test.main()


def run_like_worker(arguments: list[str]) -> int:
    import qq_like_test

    sys.argv = ["qq_like_test.py", *arguments]
    return qq_like_test.main()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    if "--run-like" in sys.argv:
        index = sys.argv.index("--run-like")
        return run_like_worker(sys.argv[index + 1 :])
    if "--run-batch" in sys.argv:
        index = sys.argv.index("--run-batch")
        return run_batch_worker(sys.argv[index + 1 :])
    app = LikeFlowApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
