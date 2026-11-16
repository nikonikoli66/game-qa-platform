"""一键启动：拉起被测游戏服 + 打开 Streamlit 看板。

用法：
    python run_demo.py           # 启动被测服 + 看板（默认）
    python run_demo.py --cli     # 不开浏览器，直接在终端跑完整流程并打印结果
    python run_demo.py --server   # 只启动被测服
    python run_demo.py --check    # 只做环境自检（依赖 / 服务 / 数据库）
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _pump_processes(procs: list[subprocess.Popen]) -> None:
    """把子进程输出直接透传到当前终端，并做存活巡检。"""
    try:
        while True:
            for p in procs:
                if p.poll() is not None:
                    print(f"\n[!] 子进程 {p.args} 已退出（code={p.returncode}），停止全部进程")
                    raise KeyboardInterrupt
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n正在停止…")
        for p in procs:
            if p.poll() is None:
                p.terminate()


def check_env() -> bool:
    ok = True
    missing = []
    for mod in ("requests", "pandas", "plotly", "streamlit", "fastapi", "uvicorn"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        ok = False
        print(f"✗ 缺少依赖: {missing}\n  请先执行: pip install -r requirements.txt")
    else:
        print("✓ Python 依赖齐全")

    from app import config
    print(f"✓ 数据库: {config.DB_URL}")
    print(f"✓ AI 引擎: {config.llm_mode_label()}")
    from app.m2_automation import env_manager

    alive = env_manager.server_alive()
    print(f"{'✓' if alive else '✗'} 被测服 {config.GAME_API_BASE}: "
          f"{'在线' if alive else '未启动（run_demo.py 会自动拉起）'}")
    return ok


def start_mock_server() -> subprocess.Popen | None:
    import os

    from app import config
    from app.m2_automation import env_manager

    if env_manager.server_alive():
        print(f"✓ 被测服已在线: {config.GAME_API_BASE}")
        return None
    env = os.environ.copy()
    env.setdefault("MOCK_REWARD_DELAY", str(config.MOCK_REWARD_DELAY))
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "mock_server.main:app",
         "--host", "127.0.0.1", "--port", str(config.MOCK_PORT), "--log-level", "warning"],
        cwd=str(ROOT), env=env,
    )
    print("… 启动被测服", end="", flush=True)
    for _ in range(60):
        if env_manager.server_alive():
            print(" ✓")
            return proc
        print(".", end="", flush=True)
        time.sleep(0.5)
    print(" ✗ 启动超时")
    return proc


def run_cli() -> int:
    from app import pipeline

    start_mock_server()
    print("\n=== 全流程开始 ===")
    info = pipeline.seed_cases(force=True)
    print(f"[1/4] 用例生成：{info['valid']}/{info['total']} 条通过质检")
    batch = pipeline.run_batch()
    print(f"[2/4] 自动化执行：{batch['passed']}/{batch['total']} 通过，"
          f"通过率 {batch['pass_rate']}%")
    for r in batch["results"]:
        mark = "✓" if r["status"] == "passed" else "✗"
        print(f"      {mark} {r['case_title']}  [{r['failure_kind']}]")
    print(f"[3/4] 缺陷归因：生成 {len(batch['defects'])} 张缺陷单")
    for d in batch["defects"]:
        print(f"      · {d['severity']} {d['title']}")
    tel = pipeline.collect_telemetry()
    exp = pipeline.experience_report()
    print(f"[4/4] 体验数据：{tel['sessions']} 场会话（{tel['mode']}）")
    print(f"      整体通关率 {exp['overall_clear_rate'] * 100:.1f}%，"
          f"难度断崖 {len(exp['cliffs'])} 处")
    print(f"      {exp['advice']['summary']}")
    print("\n=== 完成，执行 python run_demo.py 可打开看板查看细节 ===")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="AI 辅助游戏玩法自动化校验 & 体验分析平台")
    parser.add_argument("--cli", action="store_true", help="终端跑完整流程")
    parser.add_argument("--server", action="store_true", help="只启动被测服")
    parser.add_argument("--check", action="store_true", help="只做环境自检")
    parser.add_argument("--port", type=int, default=8501, help="看板端口")
    args = parser.parse_args()

    if args.check:
        return 0 if check_env() else 1
    if args.cli:
        return run_cli()

    server_proc = start_mock_server()
    if args.server:
        if server_proc:
            _pump_processes([server_proc])
        return 0

    ui_proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(ROOT / "app" / "m5_ui" / "dashboard.py"),
         "--server.port", str(args.port), "--server.headless", "true",
         "--browser.gatherUsageStats", "false"],
        cwd=str(ROOT),
    )
    time.sleep(3)
    url = f"http://localhost:{args.port}"
    print(f"\n✓ 看板已启动: {url}\n  （Ctrl+C 退出，会同时关闭被测服）")
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - 无浏览器环境不影响主流程
        pass

    procs = [p for p in (server_proc, ui_proc) if p is not None]
    _pump_processes(procs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
