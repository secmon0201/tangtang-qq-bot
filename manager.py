from __future__ import annotations

import ctypes
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from urllib.parse import quote
from pathlib import Path
from typing import Any

import psutil
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText


APP_NAME = "QQ 本地机器人管理器"
DEFAULT_BOT_QQ = "2120682836"
DEFAULT_WEBUI_HOST = "127.0.0.1"
DEFAULT_WEBUI_PORT = 6099

COLORS = {
    "window": "#f5f5f5",
    "card": "#ffffff",
    "border": "#e5e5e5",
    "text": "#1f1f1f",
    "muted": "#616161",
    "accent": "#0067c0",
    "accent_hover": "#005a9e",
    "accent_pressed": "#004578",
    "success": "#107c10",
    "warning": "#9d5d00",
    "danger": "#c42b1c",
    "log_background": "#fbfbfb",
}

ACCOUNT_HINT_PATTERNS = (
    re.compile(r"(?:^|\s)-q\s+(\d{5,12})(?:\s|$)", re.IGNORECASE),
    re.compile(r"--(?:qq|uin|uid|account)(?:=|\s+)(\d{5,12})(?:\s|$)", re.IGNORECASE),
    re.compile(r"qq(?:nt)?[_-](\d{5,12})", re.IGNORECASE),
)
WEBUI_URL_PATTERN = re.compile(
    r"(?i)https?://(?P<host>[^\s/:]+|\d{1,3}(?:\.\d{1,3}){3})(?::(?P<port>\d+))?/webui(?:/)?(?:\?[^\s]*)?"
)
WEBUI_TOKEN_PATTERN = re.compile(r"(?i)[?&]token=([A-Za-z0-9._~+\-/=]+)")
WEBUI_LABEL_TOKEN_PATTERN = re.compile(
    r"(?i)(?:webui[^\r\n]{0,120}?\btoken\b\s*[:=]\s*[\"']?)([A-Za-z0-9._~+\-/=]{6,})"
)
BOT_CONNECTED_PATTERN = re.compile(r"Bot\s+(\d{5,12})\s+connected", re.IGNORECASE)
BOT_DISCONNECTED_PATTERN = re.compile(r"Bot\s+(\d{5,12})\s+disconnected", re.IGNORECASE)


def project_root() -> Path:
    candidates = []
    if getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        candidates.extend((executable_dir, executable_dir.parent, Path.cwd()))
    else:
        candidates.append(Path(__file__).resolve().parent)
    for candidate in candidates:
        if (candidate / "bot").is_dir() and (candidate / "pyproject.toml").exists():
            return candidate
    return candidates[0]


ROOT = project_root()
ENV_PATH = ROOT / ".env"
STATE_PATH = ROOT / "data" / "manager-state.json"


class EnvFile:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lines: list[str] = []
        self.values: dict[str, str] = {}
        self.reload()

    def reload(self) -> None:
        self.lines = self.path.read_text(encoding="utf-8").splitlines() if self.path.exists() else []
        self.values = {}
        for line in self.lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            self.values[key.strip()] = value

    @staticmethod
    def format_value(value: str) -> str:
        if any(char.isspace() for char in value) or "#" in value:
            return '"' + value.replace('"', '\\"') + '"'
        return value

    def set_many(self, updates: dict[str, str]) -> None:
        remaining = dict(updates)
        output: list[str] = []
        for line in self.lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                if key in remaining:
                    output.append(f"{key}={self.format_value(remaining.pop(key))}")
                    continue
            output.append(line)
        for key, value in remaining.items():
            if output and output[-1].strip():
                output.append("")
            output.append(f"{key}={self.format_value(value)}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
        self.reload()


def parse_ids(value: str, max_count: int | None = None) -> tuple[int, ...]:
    items = [item.strip() for item in value.split(",") if item.strip()]
    if any(not item.isdigit() or int(item) <= 0 for item in items):
        raise ValueError("QQ号和群号必须是正整数，并用英文逗号分隔")
    result = tuple(dict.fromkeys(int(item) for item in items))
    if max_count is not None and len(result) > max_count:
        raise ValueError(f"最多只能配置 {max_count} 个群")
    return result


def configure_windows_dpi() -> None:
    """Opt into per-monitor DPI before Tk creates any windows."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)
    except (AttributeError, OSError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            pass


def account_hints_from_command_line(command_line: str) -> tuple[str, ...]:
    hints: set[str] = set()
    for pattern in ACCOUNT_HINT_PATTERNS:
        hints.update(pattern.findall(command_line))
    return tuple(sorted(hints))


def process_matches_account(process: dict[str, Any], account_id: str) -> bool:
    return str(account_id) in {str(item) for item in process.get("account_ids", ())}


def napcat_launch_command(launcher: Path, bot_qq: str) -> str:
    """Use CMD's standard outer-quote form for a batch path containing spaces."""
    return f'cmd.exe /d /c ""{launcher}" -q {bot_qq}"'


def extract_webui_token(text: str) -> str | None:
    """Extract only a WebUI token; never treat the OneBot token as a WebUI token."""
    if "webui" not in text.lower() and "6099" not in text:
        return None
    url_match = WEBUI_URL_PATTERN.search(text)
    if url_match:
        token_match = WEBUI_TOKEN_PATTERN.search(url_match.group(0))
        if token_match:
            return token_match.group(1)
    label_match = WEBUI_LABEL_TOKEN_PATTERN.search(text)
    return label_match.group(1) if label_match else None


def webui_settings(napcat_dir: Path, cached_token: str | None = None) -> dict[str, Any]:
    """Read WebUI settings without logging or persisting the token."""
    settings: dict[str, Any] = {
        "host": DEFAULT_WEBUI_HOST,
        "port": DEFAULT_WEBUI_PORT,
        "token": cached_token or "",
        "enabled": True,
    }
    config_path = napcat_dir / "config" / "webui.json"
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            port = data.get("port")
            if isinstance(port, int) and 1 <= port <= 65535:
                settings["port"] = port
            token = data.get("token")
            if isinstance(token, str) and token.strip():
                settings["token"] = token.strip()
            settings["enabled"] = not bool(data.get("disableWebUI", False))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return settings


def redact_sensitive_log(text: str, onebot_token: str = "") -> str:
    """Keep manager logs useful while ensuring credentials never reach the UI."""
    output = text
    if onebot_token:
        output = output.replace(onebot_token, "<hidden>")
    output = re.sub(r"(?i)([?&]token=)[^&\s]+", r"\1<hidden>", output)
    output = re.sub(
        r"(?i)([\"']?token[\"']?\s*[:=]\s*[\"']?)[^\"'\s,}]+",
        r"\1<hidden>",
        output,
    )
    return output


def process_info(pid: int) -> dict[str, Any] | None:
    try:
        process = psutil.Process(pid)
        return {"pid": pid, "name": process.name(), "create_time": process.create_time()}
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def process_identity_alive(pid: int | None, create_time: float | None) -> bool:
    if not pid:
        return False
    info = process_info(pid)
    if not info:
        return False
    return create_time is None or abs(info["create_time"] - create_time) <= 3


def qq_root_processes() -> list[dict[str, Any]]:
    processes: list[psutil.Process] = []
    qq_pids: set[int] = set()
    for process in psutil.process_iter(["pid", "name", "ppid", "cmdline"]):
        try:
            if (process.info.get("name") or "").lower() == "qq.exe":
                processes.append(process)
                qq_pids.add(process.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    roots = []
    for process in processes:
        try:
            if process.ppid() not in qq_pids:
                command_line = " ".join(process.info.get("cmdline") or [])
                roots.append(
                    {
                        "pid": process.pid,
                        "create_time": process.create_time(),
                        "account_ids": account_hints_from_command_line(command_line),
                    }
                )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return roots


def kill_process_tree(pid: int) -> tuple[bool, str]:
    try:
        process = psutil.Process(pid)
        children = process.children(recursive=True)
        for child in children:
            try:
                child.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        process.terminate()
        _, alive = psutil.wait_procs(children + [process], timeout=3)
        for item in alive:
            try:
                item.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return True, f"已停止进程树 PID {pid}"
    except psutil.NoSuchProcess:
        return True, f"进程 PID {pid} 已不存在"
    except psutil.AccessDenied:
        return False, f"无法停止 PID {pid}，请以管理员身份运行管理器"


class ManagerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("1100x780")
        self.root.minsize(900, 660)
        self.env = EnvFile(ENV_PATH)
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.bot_process: subprocess.Popen[str] | None = None
        self.bot_pid: int | None = None
        self.bot_create_time: float | None = None
        self.launch_process: subprocess.Popen[Any] | None = None
        self.napcat_pid: int | None = None
        self.napcat_create_time: float | None = None
        self.napcat_account_id: str | None = None
        self.napcat_started_at: float | None = None
        self.napcat_existing_pids: set[int] = set()
        self.napcat_capture_attempts = 0
        self.webui_token: str | None = None
        self.webui_port = DEFAULT_WEBUI_PORT
        self.connected_bot_ids: set[str] = set()
        self.vars: dict[str, tk.StringVar] = {}
        self._load_state()
        self._build_ui()
        self._refresh_fields()
        self.root.after(300, self._poll)
        self.root.protocol("WM_DELETE_WINDOW", self._close_window)

    def _load_state(self) -> None:
        try:
            state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            self.bot_pid = int(state["bot_pid"]) if state.get("bot_pid") else None
            self.bot_create_time = float(state["bot_create_time"]) if state.get("bot_create_time") else None
            self.napcat_pid = int(state["napcat_pid"]) if state.get("napcat_pid") else None
            self.napcat_create_time = float(state["napcat_create_time"]) if state.get("napcat_create_time") else None
            self.napcat_account_id = str(state["napcat_account_id"]) if state.get("napcat_account_id") else None
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            self.bot_pid = None
            self.bot_create_time = None
            self.napcat_pid = None
            self.napcat_create_time = None
            self.napcat_account_id = None

    def _save_state(self) -> None:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(
            json.dumps(
                {
                    "bot_pid": self.bot_pid,
                    "bot_create_time": self.bot_create_time,
                    "napcat_pid": self.napcat_pid,
                    "napcat_create_time": self.napcat_create_time,
                    "napcat_account_id": self.napcat_account_id,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _var(self, key: str, default: str = "") -> tk.StringVar:
        self.vars[key] = tk.StringVar(value=default)
        return self.vars[key]

    def _build_ui(self) -> None:
        style = ttk.Style(self.root)
        # clam lets us control the small set of colors needed for a Win11-like surface.
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10), foreground=COLORS["text"])
        style.configure("TFrame", background=COLORS["window"])
        style.configure("Card.TFrame", background=COLORS["card"])
        style.configure("TLabel", background=COLORS["window"], foreground=COLORS["text"])
        style.configure("Card.TLabel", background=COLORS["card"], foreground=COLORS["text"])
        style.configure("Title.TLabel", background=COLORS["window"], foreground=COLORS["text"], font=("Segoe UI", 22, "bold"))
        style.configure("Subtitle.TLabel", background=COLORS["window"], foreground=COLORS["muted"], font=("Segoe UI", 10))
        style.configure("Section.TLabel", background=COLORS["card"], foreground=COLORS["text"], font=("Segoe UI", 12, "bold"))
        style.configure("Muted.TLabel", background=COLORS["card"], foreground=COLORS["muted"], font=("Segoe UI", 9))
        style.configure("Hint.TLabel", background=COLORS["window"], foreground=COLORS["muted"], font=("Segoe UI", 9))
        style.configure("Success.TLabel", background=COLORS["card"], foreground=COLORS["success"], font=("Segoe UI", 10, "bold"))
        style.configure("Warning.TLabel", background=COLORS["card"], foreground=COLORS["warning"], font=("Segoe UI", 10, "bold"))
        style.configure("Danger.TLabel", background=COLORS["card"], foreground=COLORS["danger"], font=("Segoe UI", 10, "bold"))
        style.configure("Card.TLabelframe", background=COLORS["card"], bordercolor=COLORS["border"], relief="solid", borderwidth=1)
        style.configure("Card.TLabelframe.Label", background=COLORS["card"], foreground=COLORS["text"], font=("Segoe UI", 10, "bold"))
        style.configure("TNotebook", background=COLORS["window"], borderwidth=0, tabmargins=0)
        style.configure("TNotebook.Tab", background="#e9e9e9", foreground=COLORS["muted"], padding=(18, 10), borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", COLORS["card"])], foreground=[("selected", COLORS["accent"])])
        style.configure("TButton", padding=(14, 8), background="#ffffff", foreground=COLORS["text"], bordercolor=COLORS["border"], relief="solid", borderwidth=1)
        style.map("TButton", background=[("active", "#f0f6fc"), ("pressed", "#e5f1fb")])
        style.configure("Primary.TButton", padding=(16, 9), background=COLORS["accent"], foreground="#ffffff", bordercolor=COLORS["accent"], font=("Segoe UI", 10, "bold"))
        style.map("Primary.TButton", background=[("active", COLORS["accent_hover"]), ("pressed", COLORS["accent_pressed"])], foreground=[("disabled", "#d7e6f5")])
        style.configure("Danger.TButton", padding=(14, 8), background="#fff5f5", foreground=COLORS["danger"], bordercolor="#f0c5c0")
        style.map("Danger.TButton", background=[("active", "#ffe8e5"), ("pressed", "#ffd8d3")])
        style.configure("TEntry", padding=(9, 7), fieldbackground="#ffffff", foreground=COLORS["text"], bordercolor=COLORS["border"])
        style.configure("TCheckbutton", background=COLORS["card"], foreground=COLORS["text"])

        self.root.configure(background=COLORS["window"])
        self.root.option_add("*TCombobox*Listbox.font", "Segoe UI 10")

        header = ttk.Frame(self.root, padding=(28, 24, 28, 14))
        header.pack(fill="x")
        ttk.Label(header, text=APP_NAME, style="Title.TLabel").pack(anchor="w")
        ttk.Label(header, text="本地运行控制台  ·  配置写入 .env  ·  数据保存在本机", style="Subtitle.TLabel").pack(anchor="w", pady=(4, 0))

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=22, pady=(0, 22))
        control_tab = ttk.Frame(notebook, padding=16)
        config_tab = ttk.Frame(notebook, padding=18)
        help_tab = ttk.Frame(notebook, padding=18)
        notebook.add(control_tab, text="运行控制")
        notebook.add(config_tab, text="基础配置")
        notebook.add(help_tab, text="使用帮助")

        self._build_control_tab(control_tab)
        self._build_config_tab(config_tab)
        self._build_help_tab(help_tab)

    def _build_control_tab(self, parent: ttk.Frame) -> None:
        status_frame = ttk.LabelFrame(parent, text="运行状态", style="Card.TLabelframe", padding=16)
        status_frame.pack(fill="x")
        status_frame.columnconfigure(0, weight=1)
        status_frame.columnconfigure(1, weight=1)
        self.bot_status = ttk.Label(status_frame, text="● 机器人：未运行", style="Warning.TLabel")
        self.bot_status.grid(row=0, column=0, sticky="w", padx=(0, 26))
        self.napcat_status = ttk.Label(status_frame, text="● NapCat：未托管", style="Warning.TLabel")
        self.napcat_status.grid(row=0, column=1, sticky="w")

        address_frame = ttk.LabelFrame(parent, text="本机接口", style="Card.TLabelframe", padding=16)
        address_frame.pack(fill="x", pady=(14, 0))
        address_frame.columnconfigure(0, weight=1)
        self.host_label = ttk.Label(address_frame, text="机器人 HTTP：", style="Card.TLabel")
        self.host_label.grid(row=0, column=0, sticky="w")
        self.ws_label = ttk.Label(address_frame, text="OneBot WebSocket：", style="Card.TLabel")
        self.ws_label.grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.webui_label = ttk.Label(address_frame, text="NapCat WebUI：", style="Card.TLabel")
        self.webui_label.grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.webui_status = ttk.Label(address_frame, text="Token 自动识别：等待 NapCat", style="Muted.TLabel")
        self.webui_status.grid(row=3, column=0, sticky="w", pady=(4, 0))
        button_frame = ttk.Frame(address_frame, style="Card.TFrame")
        button_frame.grid(row=0, column=1, rowspan=4, sticky="e", padx=(18, 0))
        ttk.Button(button_frame, text="打开 NapCat 目录", command=self.open_napcat_dir).pack(anchor="e")
        ttk.Button(button_frame, text="打开 NapCat WebUI", style="Primary.TButton", command=self.open_webui).pack(anchor="e", pady=(9, 0))

        actions = ttk.Frame(parent)
        actions.pack(fill="x", pady=14)
        ttk.Button(actions, text="启动机器人和 NapCat", style="Primary.TButton", command=self.start_all).pack(side="left")
        ttk.Button(actions, text="停止机器人", command=self.stop_bot).pack(side="left", padx=(10, 0))
        ttk.Button(actions, text="停止机器人和 NapCat", style="Danger.TButton", command=self.stop_all).pack(side="left", padx=(10, 0))
        ttk.Button(actions, text="保存配置", command=self.save_config).pack(side="right")

        log_frame = ttk.LabelFrame(parent, text="运行日志（敏感 Token 已隐藏）", style="Card.TLabelframe", padding=10)
        log_frame.pack(fill="both", expand=True)
        self.log_text = ScrolledText(
            log_frame,
            state="disabled",
            wrap="none",
            font=("Cascadia Mono", 9),
            background=COLORS["log_background"],
            foreground=COLORS["text"],
            insertbackground=COLORS["text"],
            relief="flat",
            borderwidth=0,
            padx=10,
            pady=8,
        )
        self.log_text.pack(fill="both", expand=True)

    def _build_config_tab(self, parent: ttk.Frame) -> None:
        fields = [
            ("MANAGED_GROUP_IDS", "初始管理群（不限数量，逗号分隔）"),
            ("BOT_OPERATOR_IDS", "操作者 QQ 号（逗号分隔）"),
            ("NAPCAT_QQ_ID", "机器人 QQ 号"),
            ("NAPCAT_DIR", "NapCat.Shell 目录"),
            ("HOST", "机器人监听地址"),
            ("PORT", "机器人端口"),
            ("BOT_ROLLUP_HOUR", "统计结算小时"),
            ("BOT_ROLLUP_MINUTE", "统计结算分钟"),
            ("ONEBOT_ACCESS_TOKEN", "OneBot Token（隐藏显示）"),
        ]
        for row, (key, label) in enumerate(fields):
            ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 18), pady=7)
            variable = self._var(key)
            show = "*" if key == "ONEBOT_ACCESS_TOKEN" else ""
            entry = ttk.Entry(parent, textvariable=variable, show=show, width=76)
            entry.grid(row=row, column=1, sticky="ew", pady=7)
            if key == "NAPCAT_DIR":
                ttk.Button(parent, text="选择目录", command=self.choose_napcat_dir).grid(row=row, column=2, padx=(10, 0))
        parent.columnconfigure(1, weight=1)
        ttk.Label(
            parent,
            text="修改后点击“保存配置”，再次启动机器人才会应用。Token 只在本机显示为隐藏字符。",
            style="Hint.TLabel",
        ).grid(row=len(fields), column=0, columnspan=3, sticky="w", pady=(18, 0))

    def _build_help_tab(self, parent: ttk.Frame) -> None:
        help_text = ScrolledText(
            parent,
            wrap="word",
            font=("Segoe UI", 10),
            background=COLORS["card"],
            foreground=COLORS["text"],
            insertbackground=COLORS["text"],
            relief="flat",
            borderwidth=0,
            padx=18,
            pady=16,
        )
        help_text.pack(fill="both", expand=True)
        help_text.insert(
            "1.0",
            """启动顺序
1. 点击“一键启动”。
2. 管理器先启动机器人，再调用 NapCat.Shell\\launcher.bat。
3. NapCat 如果请求管理员权限，请在弹出的权限窗口中确认。
4. NapCat 的 WebSocket 客户端应连接到页面顶部显示的地址。
5. 在测试群发送 #机器人状态 验证连接。

常用命令
#机器人状态
#发言排行 日
#A海岸发言排行 周
#查重 群号1 群号2
#白名单 添加 QQ号 备注
#白名单 删除 QQ号
#原神 绑定 UID
#原神

停止顺序
点击“停止全部”。管理器只会停止自己记录的 NapCat QQ 进程树；无法确认归属时会保留 QQ，避免关闭其他账号。

注意
- 不要把 ws:// 地址输入 PowerShell，它应填写在 NapCat WebSocket 客户端配置中。
- 不要使用 NapCat.Shell\\KillQQ.bat，它会强制关闭所有 QQ.exe。
- 不要把 QQ 密码、验证码或 Token 发到聊天中。
""",
        )
        help_text.configure(state="disabled")

    def _refresh_fields(self) -> None:
        defaults = {
            "MANAGED_GROUP_IDS": "",
            "BOT_OPERATOR_IDS": "",
            "NAPCAT_QQ_ID": DEFAULT_BOT_QQ,
            "NAPCAT_DIR": str(ROOT / "NapCat.Shell"),
            "HOST": "127.0.0.1",
            "PORT": "8080",
            "BOT_ROLLUP_HOUR": "1",
            "BOT_ROLLUP_MINUTE": "5",
            "ONEBOT_ACCESS_TOKEN": "",
        }
        for key, default in defaults.items():
            self.vars[key].set(self.env.values.get(key, default))
        self._refresh_address_labels()

    def _refresh_address_labels(self) -> None:
        host = self.vars.get("HOST", tk.StringVar(value="127.0.0.1")).get() or "127.0.0.1"
        port = self.vars.get("PORT", tk.StringVar(value="8080")).get() or "8080"
        self.host_label.configure(text=f"机器人 HTTP：http://{host}:{port}")
        self.ws_label.configure(text=f"OneBot WebSocket：ws://{host}:{port}/onebot/v11/ws")
        settings = webui_settings(Path(self.vars.get("NAPCAT_DIR", tk.StringVar()).get()), self.webui_token)
        self.webui_token = settings.get("token") or None
        self.webui_port = int(settings.get("port") or DEFAULT_WEBUI_PORT)
        self.webui_label.configure(text=f"NapCat WebUI：http://{DEFAULT_WEBUI_HOST}:{self.webui_port}/webui")
        if settings.get("token"):
            self.webui_status.configure(text="Token 自动识别：已就绪（原文不会显示）", style="Success.TLabel")
        else:
            self.webui_status.configure(text="Token 自动识别：启动 NapCat 后读取", style="Muted.TLabel")

    def _append_log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        safe_message = redact_sensitive_log(message, self.vars.get("ONEBOT_ACCESS_TOKEN", tk.StringVar()).get())
        self.log_text.insert("end", safe_message.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _handle_log_line(self, line: str) -> None:
        token = extract_webui_token(line)
        if token:
            self.webui_token = token
            self.webui_status.configure(text="Token 自动识别：已从 NapCat 启动输出读取", style="Success.TLabel")
        url_match = WEBUI_URL_PATTERN.search(line)
        if url_match:
            port = url_match.group("port")
            if port and port.isdigit():
                self.webui_port = int(port)
            self._refresh_address_labels()
        connected = BOT_CONNECTED_PATTERN.search(line)
        if connected:
            self.connected_bot_ids.add(connected.group(1))
        disconnected = BOT_DISCONNECTED_PATTERN.search(line)
        if disconnected:
            self.connected_bot_ids.discard(disconnected.group(1))
        self._append_log(line)

    def _reader(self, stream: Any) -> None:
        try:
            for line in iter(stream.readline, ""):
                self.log_queue.put(line)
        finally:
            stream.close()

    def _poll(self) -> None:
        while True:
            try:
                self._handle_log_line(self.log_queue.get_nowait())
            except queue.Empty:
                break
        if self.bot_process and self.bot_process.poll() is not None:
            self._append_log(f"机器人进程已退出，返回码：{self.bot_process.returncode}")
            self.bot_process = None
            self.bot_pid = None
            self.bot_create_time = None
            self.connected_bot_ids.clear()
            self._save_state()
        if process_identity_alive(self.bot_pid, self.bot_create_time):
            mode = "本管理器" if self.bot_process else "已接管"
            self.bot_status.configure(text=f"● 机器人：运行中（PID {self.bot_pid}，{mode}）", style="Success.TLabel")
        else:
            self.bot_status.configure(text="● 机器人：未运行", style="Warning.TLabel")
        if self.napcat_pid and process_info(self.napcat_pid):
            self.napcat_status.configure(text=f"● NapCat：运行中（QQ PID {self.napcat_pid}）", style="Success.TLabel")
        elif self.napcat_started_at:
            self.napcat_status.configure(text="● NapCat：已请求启动，等待 QQ 进程", style="Warning.TLabel")
        else:
            self.napcat_status.configure(text="● NapCat：未托管", style="Warning.TLabel")
        self.root.after(500, self._poll)

    def validate_config(self) -> tuple[bool, str]:
        try:
            parse_ids(self.vars["MANAGED_GROUP_IDS"].get(), max_count=10)
            parse_ids(self.vars["BOT_OPERATOR_IDS"].get())
            parse_ids(self.vars["NAPCAT_QQ_ID"].get())
            port = int(self.vars["PORT"].get())
            hour = int(self.vars["BOT_ROLLUP_HOUR"].get())
            minute = int(self.vars["BOT_ROLLUP_MINUTE"].get())
            if not 1 <= port <= 65535:
                raise ValueError("端口必须在 1-65535 之间")
            if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                raise ValueError("结算时间不合法")
        except ValueError as exc:
            return False, str(exc)
        return True, ""

    def save_config(self) -> bool:
        valid, error = self.validate_config()
        if not valid:
            messagebox.showerror("配置错误", error)
            return False
        updates = {key: variable.get().strip() for key, variable in self.vars.items()}
        self.env.set_many(updates)
        os.environ.update(updates)
        self._refresh_address_labels()
        self._append_log("配置已保存到 .env")
        return True

    def start_all(self) -> None:
        if not self.save_config():
            return
        self.start_bot()
        # Give the local OneBot server time to accept an already-running NapCat connection.
        self.root.after(2500, self.start_napcat)

    def start_bot(self) -> None:
        if self.bot_process and self.bot_process.poll() is None:
            self._append_log("机器人已经在运行，跳过重复启动")
            return
        existing = self.find_existing_bot()
        if existing:
            self.bot_pid = existing["pid"]
            self.bot_create_time = existing["create_time"]
            self._save_state()
            self._append_log(f"检测到已有机器人进程，已接管 PID {self.bot_pid}，跳过重复启动")
            return
        self.connected_bot_ids.clear()
        env = os.environ.copy()
        env.update(self.env.values)
        env["QQ_BOT_ROOT"] = str(ROOT)
        if getattr(sys, "frozen", False):
            command = [sys.executable, "--bot"]
        else:
            python = ROOT / ".venv" / "Scripts" / "python.exe"
            command = [str(python if python.exists() else sys.executable), "-u", "-m", "bot"]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self.bot_process = subprocess.Popen(
                command,
                cwd=ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=flags,
            )
        except OSError as exc:
            messagebox.showerror("启动失败", str(exc))
            return
        threading.Thread(target=self._reader, args=(self.bot_process.stdout,), daemon=True).start()  # type: ignore[arg-type]
        self.bot_pid = self.bot_process.pid
        info = process_info(self.bot_pid)
        self.bot_create_time = info["create_time"] if info else None
        self._save_state()
        self._append_log(f"机器人已启动，PID {self.bot_process.pid}")

    def find_existing_bot(self) -> dict[str, Any] | None:
        for process in psutil.process_iter(["pid", "cmdline", "create_time"]):
            try:
                if process.pid == os.getpid():
                    continue
                command_line = " ".join(process.info.get("cmdline") or [])
                process_cwd = process.cwd()
                if ("-m bot" in command_line or "--bot" in command_line) and str(ROOT).lower() in process_cwd.lower():
                    return {
                        "pid": process.pid,
                        "create_time": process.info.get("create_time") or process.create_time(),
                    }
            except (psutil.NoSuchProcess, psutil.AccessDenied, FileNotFoundError):
                continue
        return None

    def start_napcat(self) -> None:
        napcat_dir = Path(self.vars["NAPCAT_DIR"].get()).expanduser()
        launcher = napcat_dir / "launcher.bat"
        if not launcher.exists():
            messagebox.showerror("NapCat 路径错误", f"找不到启动脚本：{launcher}")
            return
        bot_qq = self.vars["NAPCAT_QQ_ID"].get().strip()
        existing = qq_root_processes()
        verified = [item for item in existing if process_matches_account(item, bot_qq)]
        # A OneBot self_id is stronger evidence than a generic QQ.exe process name.
        if not verified and bot_qq in self.connected_bot_ids and len(existing) == 1:
            verified = existing
        if verified:
            selected = verified[0]
            self.napcat_pid = int(selected["pid"])
            self.napcat_create_time = float(selected["create_time"])
            self.napcat_account_id = bot_qq
            self.napcat_started_at = None
            self._save_state()
            self._append_log(f"已确认现有 QQ 是机器人账号，纳入停止管理：PID {self.napcat_pid}")
            return
        if existing:
            pids = ", ".join(str(item["pid"]) for item in existing)
            self._append_log(
                f"发现已有 QQ 进程（PID {pids}），但未确认账号为机器人 QQ {bot_qq}；不会接管或停止它，继续按目标账号启动 NapCat。"
            )
        self.napcat_existing_pids = {int(item["pid"]) for item in existing}
        self.napcat_capture_attempts = 0
        self.napcat_started_at = time.time()
        try:
            self.launch_process = subprocess.Popen(
                napcat_launch_command(launcher, bot_qq),
                cwd=napcat_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
            if self.launch_process.stdout is not None:
                threading.Thread(target=self._reader, args=(self.launch_process.stdout,), daemon=True).start()
            self._append_log(f"已调用 NapCat：{launcher}")
            self.root.after(4000, self._capture_napcat_pid)
        except OSError as exc:
            messagebox.showerror("NapCat 启动失败", str(exc))

    def _capture_napcat_pid(self) -> None:
        if not self.napcat_started_at:
            return
        self.napcat_capture_attempts += 1
        bot_qq = self.vars["NAPCAT_QQ_ID"].get().strip()
        candidates = [
            item
            for item in qq_root_processes()
            if int(item["pid"]) not in self.napcat_existing_pids
            and item["create_time"] >= self.napcat_started_at - 3
        ]
        if candidates:
            if bot_qq not in self.connected_bot_ids:
                if self.napcat_capture_attempts < 10:
                    self._append_log("已找到新 QQ 进程，等待 OneBot 确认登录账号后再接管。")
                    self.root.after(3000, self._capture_napcat_pid)
                else:
                    self._append_log("未收到目标 QQ 的 OneBot 身份确认，未接管新 QQ 进程；请检查登录和 WebSocket 配置。")
                    self.napcat_started_at = None
                return
            selected = max(candidates, key=lambda item: item["create_time"])
            self.napcat_pid = int(selected["pid"])
            self.napcat_create_time = float(selected["create_time"])
            self.napcat_account_id = bot_qq
            self.napcat_started_at = None
            self._save_state()
            self._append_log(f"已确认并托管 NapCat QQ 进程，PID {self.napcat_pid}")
        else:
            if self.napcat_capture_attempts < 10:
                self._append_log("暂未找到新 NapCat QQ 进程，继续等待管理员确认或人工登录。")
                self.root.after(3000, self._capture_napcat_pid)
            else:
                self._append_log("暂未找到新 NapCat QQ 进程，请检查 NapCat 控制台是否需要管理员确认或人工登录。")
                self.napcat_started_at = None

    def stop_bot(self) -> None:
        if process_identity_alive(self.bot_pid, self.bot_create_time):
            ok, detail = kill_process_tree(self.bot_pid)
            self._append_log(detail)
            if not ok:
                messagebox.showwarning("停止失败", detail)
            self.bot_process = None
            self.bot_pid = None
            self.bot_create_time = None
            self._save_state()
            return
        self._append_log("机器人当前未由本管理器运行")

    def stop_napcat(self) -> None:
        if not self.napcat_pid or self.napcat_create_time is None:
            self._append_log("没有可安全确认归属的 NapCat 进程，未自动关闭 QQ。")
            return
        bot_qq = self.vars["NAPCAT_QQ_ID"].get().strip()
        if self.napcat_account_id != bot_qq:
            self._append_log("NapCat 进程没有保存的目标 QQ 身份确认，未自动关闭 QQ。")
            return
        info = process_info(self.napcat_pid)
        if not info or info["name"].lower() != "qq.exe" or abs(info["create_time"] - self.napcat_create_time) > 3:
            self._append_log("NapCat 进程已不存在或 PID 已复用，未执行关闭。")
            self.napcat_pid = None
            self.napcat_create_time = None
            self.napcat_account_id = None
            self._save_state()
            return
        ok, detail = kill_process_tree(self.napcat_pid)
        self._append_log(detail)
        if not ok:
            messagebox.showwarning("NapCat 停止失败", detail)
        else:
            self.napcat_pid = None
            self.napcat_create_time = None
            self.napcat_account_id = None
            self._save_state()

    def stop_all(self) -> None:
        if not messagebox.askyesno("停止全部", "停止管理器托管的 NapCat 和机器人进程？\n不会删除数据库或配置。"):
            return
        self.stop_napcat()
        self.stop_bot()

    def choose_napcat_dir(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.vars["NAPCAT_DIR"].get() or str(ROOT))
        if selected:
            self.vars["NAPCAT_DIR"].set(selected)

    def open_napcat_dir(self) -> None:
        path = Path(self.vars["NAPCAT_DIR"].get())
        if path.exists():
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            messagebox.showerror("目录不存在", str(path))

    def open_webui(self) -> None:
        napcat_dir = Path(self.vars["NAPCAT_DIR"].get()).expanduser()
        settings = webui_settings(napcat_dir, self.webui_token)
        if not settings.get("enabled", True):
            messagebox.showwarning("NapCat WebUI 已关闭", "NapCat 配置中 disableWebUI=true，请先启用 WebUI。")
            return
        token = str(settings.get("token") or "").strip()
        self.webui_token = token or None
        self.webui_port = int(settings.get("port") or DEFAULT_WEBUI_PORT)
        self._refresh_address_labels()
        base_url = f"http://{DEFAULT_WEBUI_HOST}:{self.webui_port}/webui"
        if not token:
            messagebox.showwarning("未检测到 WebUI Token", "请先启动 NapCat，管理器会从配置或启动输出自动读取 Token。")
            return
        url = f"{base_url}?token={quote(token, safe='')}"
        webbrowser.open(url)
        self._append_log(f"已打开 NapCat WebUI：{base_url}（Token 已自动附加且不会显示）")

    def _close_window(self) -> None:
        if process_identity_alive(self.bot_pid, self.bot_create_time):
            if not messagebox.askyesno("退出管理器", "机器人仍在运行。退出管理器但保持后台服务？"):
                return
        self.root.destroy()


def print_check() -> int:
    env = EnvFile(ENV_PATH)
    groups = env.values.get("MANAGED_GROUP_IDS", "")
    operators = env.values.get("BOT_OPERATOR_IDS", "")
    napcat_dir = Path(env.values.get("NAPCAT_DIR", str(ROOT / "NapCat.Shell")))
    host = env.values.get("HOST", "127.0.0.1")
    port = env.values.get("PORT", "8080")
    webui = webui_settings(napcat_dir)
    print(f"root={ROOT}")
    print(f"groups={groups}")
    print(f"operators={operators}")
    print(f"napcat_dir={napcat_dir}")
    print(f"napcat_exists={napcat_dir.exists()}")
    print(f"launcher_exists={(napcat_dir / 'launcher.bat').exists()}")
    print(f"http=http://{host}:{port}")
    print(f"websocket=ws://{host}:{port}/onebot/v11/ws")
    print(f"webui=http://{DEFAULT_WEBUI_HOST}:{webui['port']}/webui")
    print(f"webui_token_detected={bool(webui.get('token'))}")
    return 0


def ensure_bot_streams() -> None:
    if sys.stdout is not None and sys.stderr is not None:
        return
    log_path = ROOT / "logs" / "manager-bot.out.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stream = log_path.open("a", encoding="utf-8", buffering=1)
    sys.stdout = stream
    sys.stderr = stream


def main() -> int:
    if "--bot" in sys.argv:
        try:
            os.chdir(ROOT)
            os.environ.setdefault("QQ_BOT_ROOT", str(ROOT))
            ensure_bot_streams()
            from bot.__main__ import main as bot_main

            bot_main()
            return 0
        except BaseException:
            log_path = ROOT / "logs" / "manager-bot-startup.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(traceback.format_exc(), encoding="utf-8")
            return 1
    if "--check" in sys.argv:
        return print_check()
    configure_windows_dpi()
    app_root = tk.Tk()
    ManagerApp(app_root)
    app_root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
