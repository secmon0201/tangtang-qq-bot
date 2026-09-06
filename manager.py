from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Any

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText


APP_NAME = "QQ 本地机器人管理器"
DEFAULT_WEBUI_HOST = "127.0.0.1"
DEFAULT_WEBUI_PORT = 5099


def project_root() -> Path:
    if getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        candidates = [executable_dir, executable_dir.parent, Path.cwd()]
    else:
        candidates = [Path(__file__).resolve().parent]
    for candidate in candidates:
        if (candidate / "bot").is_dir() and (candidate / "pyproject.toml").exists():
            return candidate
    return candidates[0]


ROOT = project_root()
ENV_PATH = ROOT / ".env"


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
        raise ValueError(f"最多只能配置 {max_count} 个值")
    return result


def redact_sensitive_log(text: str, secrets: tuple[str, ...] = ()) -> str:
    output = text
    for secret in secrets:
        if secret:
            output = output.replace(secret, "<hidden>")
    return output


def snowluma_webui_url(port: int, host: str = DEFAULT_WEBUI_HOST) -> str:
    if not 1 <= int(port) <= 65535:
        raise ValueError("SnowLuma WebUI 端口必须在 1-65535 之间")
    return f"http://{host}:{int(port)}"


def powershell_script_command(script: Path, *arguments: str) -> list[str]:
    engine = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if not engine:
        raise RuntimeError("找不到 PowerShell")
    return [
        engine,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        *arguments,
    ]


class ManagerApp:
    CONFIG_FIELDS = (
        ("QQ_ACCOUNT_ID", "机器人 QQ 号"),
        ("SNOWLUMA_DIR", "SnowLuma 目录"),
        ("SNOWLUMA_WEBUI_PORT", "SnowLuma WebUI 端口"),
        ("MANAGED_GROUP_IDS", "初始管理群（逗号分隔）"),
        ("BOT_OPERATOR_IDS", "超级管理员（逗号分隔）"),
        ("HOST", "NoneBot 监听地址"),
        ("PORT", "NoneBot / OneBot 端口"),
    )

    def __init__(self, window: tk.Tk) -> None:
        self.window = window
        self.window.title(APP_NAME)
        self.window.geometry("1050x760")
        self.window.minsize(880, 640)
        self.env = EnvFile(ENV_PATH)
        self.vars: dict[str, tk.StringVar] = {}
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.operation_running = False
        self._build_ui()
        self._load_fields()
        self.window.after(250, self._poll_events)
        self.refresh_status()

    def _build_ui(self) -> None:
        style = ttk.Style(self.window)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        outer = ttk.Frame(self.window, padding=20)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="QQ 本地机器人", font=("Microsoft YaHei UI", 20, "bold")).pack(anchor="w")
        ttk.Label(
            outer,
            text="SnowLuma / OneBot v11 / NoneBot2 统一运维",
            foreground="#666666",
        ).pack(anchor="w", pady=(2, 14))

        status_frame = ttk.LabelFrame(outer, text="运行状态", padding=12)
        status_frame.pack(fill="x")
        self.transport_status = ttk.Label(status_frame, text="QQ 传输：检查中")
        self.transport_status.pack(anchor="w")
        self.onebot_status = ttk.Label(status_frame, text="OneBot：检查中")
        self.onebot_status.pack(anchor="w", pady=(4, 0))
        self.webui_status = ttk.Label(status_frame, text="SnowLuma WebUI：检查中")
        self.webui_status.pack(anchor="w", pady=(4, 0))

        config_frame = ttk.LabelFrame(outer, text="本机配置", padding=12)
        config_frame.pack(fill="x", pady=(12, 0))
        config_frame.columnconfigure(1, weight=1)
        for row, (key, label) in enumerate(self.CONFIG_FIELDS):
            ttk.Label(config_frame, text=label).grid(row=row, column=0, sticky="w", pady=4)
            variable = tk.StringVar()
            self.vars[key] = variable
            ttk.Entry(config_frame, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=(12, 8), pady=4)
            if key == "SNOWLUMA_DIR":
                ttk.Button(config_frame, text="选择", command=self.choose_snowluma_dir).grid(row=row, column=2, pady=4)

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(12, 0))
        ttk.Button(actions, text="保存配置", command=self.save_config).pack(side="left")
        ttk.Button(actions, text="启动全部", command=self.start_all).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="重启全部", command=self.restart_all).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="仅重启机器人", command=self.restart_bot).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="停止全部", command=self.stop_all).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="刷新状态", command=self.refresh_status).pack(side="right")
        ttk.Button(actions, text="打开 SnowLuma WebUI", command=self.open_webui).pack(side="right", padx=(0, 8))

        self.log = ScrolledText(
            outer,
            height=16,
            wrap="word",
            font=("Consolas", 9),
            state="disabled",
        )
        self.log.pack(fill="both", expand=True, pady=(12, 0))

    def _load_fields(self) -> None:
        defaults = {
            "QQ_ACCOUNT_ID": "",
            "SNOWLUMA_DIR": str(ROOT / "SnowLuma"),
            "SNOWLUMA_WEBUI_PORT": str(DEFAULT_WEBUI_PORT),
            "MANAGED_GROUP_IDS": "",
            "BOT_OPERATOR_IDS": "",
            "HOST": "127.0.0.1",
            "PORT": "8080",
        }
        for key, variable in self.vars.items():
            variable.set(self.env.values.get(key, defaults[key]))

    def _append_log(self, message: str) -> None:
        safe = redact_sensitive_log(
            message,
            (
                self.env.values.get("ONEBOT_ACCESS_TOKEN", ""),
                self.env.values.get("TANGTANG_API_KEY", ""),
            ),
        )
        self.log.configure(state="normal")
        self.log.insert("end", safe.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def validate_config(self) -> tuple[bool, str]:
        try:
            account = parse_ids(self.vars["QQ_ACCOUNT_ID"].get(), max_count=1)
            if len(account) != 1:
                raise ValueError("机器人 QQ 号必须填写一个账号")
            parse_ids(self.vars["MANAGED_GROUP_IDS"].get())
            operators = parse_ids(self.vars["BOT_OPERATOR_IDS"].get())
            if not operators:
                raise ValueError("至少填写一个超级管理员")
            port = int(self.vars["PORT"].get())
            webui_port = int(self.vars["SNOWLUMA_WEBUI_PORT"].get())
            if not 1 <= port <= 65535 or not 1 <= webui_port <= 65535:
                raise ValueError("端口必须在 1-65535 之间")
            if port == webui_port:
                raise ValueError("SnowLuma WebUI 端口不能与 OneBot 端口相同")
        except ValueError as exc:
            return False, str(exc)
        return True, ""

    def save_config(self) -> bool:
        valid, detail = self.validate_config()
        if not valid:
            messagebox.showerror("配置错误", detail)
            return False
        updates = {key: variable.get().strip() for key, variable in self.vars.items()}
        self.env.set_many(updates)
        self._append_log("配置已保存；现有 SQLite 数据与群参数未修改。")
        return True

    def run_script(self, name: str, *arguments: str) -> None:
        if self.operation_running:
            messagebox.showinfo("操作进行中", "请等待当前操作结束。")
            return
        script = ROOT / "scripts" / name
        if not script.is_file():
            messagebox.showerror("脚本不存在", str(script))
            return
        self.operation_running = True
        self._append_log(f"> {name} {' '.join(arguments)}".rstrip())

        def worker() -> None:
            try:
                completed = subprocess.run(
                    powershell_script_command(script, *arguments),
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=600,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    check=False,
                )
                output = "\n".join(part for part in (completed.stdout, completed.stderr) if part.strip())
                self.events.put(("operation", (name, completed.returncode, output)))
            except (OSError, subprocess.SubprocessError) as exc:
                self.events.put(("operation", (name, -1, str(exc))))

        threading.Thread(target=worker, daemon=True).start()

    def restart_bot(self) -> None:
        if not self.save_config():
            return
        self.run_script("restart_bot.ps1")

    def start_all(self) -> None:
        if not self.save_config():
            return
        self.run_script("start_all.ps1")

    def restart_all(self) -> None:
        if not self.save_config():
            return
        self.run_script("restart_all.ps1")

    def stop_all(self) -> None:
        if messagebox.askyesno("停止全部", "停止本项目托管的机器人、QQ 传输、Core、隧道和 watchdog？\n不会删除数据库、参数或登录文件。"):
            self.run_script("stop_all.ps1")

    def choose_snowluma_dir(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.vars["SNOWLUMA_DIR"].get() or str(ROOT))
        if selected:
            self.vars["SNOWLUMA_DIR"].set(selected)

    def open_webui(self) -> None:
        try:
            port = int(self.vars["SNOWLUMA_WEBUI_PORT"].get())
            url = snowluma_webui_url(port)
        except ValueError as exc:
            messagebox.showerror("地址错误", str(exc))
            return
        webbrowser.open(url)
        self._append_log(f"已打开 SnowLuma WebUI：{url}")

    def refresh_status(self) -> None:
        script = ROOT / "scripts" / "status_qq_transport.ps1"

        def worker() -> None:
            try:
                completed = subprocess.run(
                    powershell_script_command(script, "-Json"),
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=20,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    check=False,
                )
                line = completed.stdout.strip().splitlines()[-1] if completed.stdout.strip() else "{}"
                payload = json.loads(line)
                self.events.put(("status", payload))
            except (OSError, ValueError, IndexError, subprocess.SubprocessError) as exc:
                self.events.put(("status_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_events(self) -> None:
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "operation":
                name, code, output = payload
                self.operation_running = False
                if output:
                    self._append_log(output)
                self._append_log(f"{name} 完成，退出码 {code}。")
                self.refresh_status()
            elif kind == "status":
                self.transport_status.configure(
                    text=f"QQ 传输：{payload.get('transport', '未知')}，进程 {payload.get('transport_process_count', 0)} 个"
                )
                self.onebot_status.configure(
                    text=f"OneBot：监听 {'在线' if payload.get('nonebot_listener') else '离线'}，客户端 {payload.get('onebot_connection_count', 0)} 个"
                )
                webui = payload.get("snowluma_webui")
                self.webui_status.configure(
                    text=f"SnowLuma WebUI：{'在线' if webui else '离线' if webui is not None else '未启用'}"
                )
            elif kind == "status_error":
                self.transport_status.configure(text="QQ 传输：状态检查失败")
                self._append_log(f"状态检查失败：{payload}")
        self.window.after(250, self._poll_events)


def print_check() -> int:
    env = EnvFile(ENV_PATH)
    account = env.values.get("QQ_ACCOUNT_ID", "")
    transport = env.values.get("QQ_PLATFORM_TRANSPORT", "snowluma").strip().lower()
    snowluma_dir = Path(env.values.get("SNOWLUMA_DIR", str(ROOT / "SnowLuma")))
    webui_port = int(env.values.get("SNOWLUMA_WEBUI_PORT", str(DEFAULT_WEBUI_PORT)))
    print(f"root={ROOT}")
    print(f"qq_platform_transport={transport}")
    print(f"account_configured={bool(account)}")
    print(f"managed_group_count={len(parse_ids(env.values.get('MANAGED_GROUP_IDS', '')))}")
    print(f"operator_count={len(parse_ids(env.values.get('BOT_OPERATOR_IDS', '')))}")
    print(f"snowluma_dir={snowluma_dir}")
    print(f"snowluma_installed={(snowluma_dir / 'index.mjs').is_file()}")
    print(f"webui={snowluma_webui_url(webui_port)}")
    return 0


def main() -> int:
    if "--check" in sys.argv:
        return print_check()
    os.environ.setdefault("QQ_BOT_ROOT", str(ROOT))
    window = tk.Tk()
    ManagerApp(window)
    window.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
