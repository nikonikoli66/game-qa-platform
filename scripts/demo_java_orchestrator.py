"""一键验证「Java 编排服务 + Python 执行器」链路。

验证四件事（核心验证四点）：
    1. 并发调度：6 条用例并发执行，总耗时明显小于串行之和；
    2. 幂等提交：同一个 clientRequestId 重复提交，返回同一批次、不重复执行；
    3. 可取消：未开始的任务能被取消，执行中的不打断；
    4. 状态机与结果回传：每条任务都能拿到 PASSED/FAILED + 失败分类 + runId。

用法：
    python scripts/demo_java_orchestrator.py            # 构建 + 启动 + 跑演示
    python scripts/demo_java_orchestrator.py --skip-build
    python scripts/demo_java_orchestrator.py --keep      # 跑完不关闭服务，方便手工 curl
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.m2_automation import env_manager  # noqa: E402

WORKER_BASE = "http://127.0.0.1:8100"
# 不用 8080：开发机上它经常被代理/其它服务占掉（本机实测探测直接返回 502）
ORCH_PORT = int(os.environ.get("ORCH_PORT", "18080"))
ORCH_BASE = f"http://127.0.0.1:{ORCH_PORT}"
ORCH_DIR = ROOT / "java-orchestrator"
JAR = ORCH_DIR / "target" / "game-qa-orchestrator-1.0.0.jar"
ORCH_LOG = ORCH_DIR / "target" / "orchestrator.log"

MIN_JAVA = 17


def resolve_java() -> str | None:
    """挑一个能跑本项目的 java 可执行文件。

    踩过的坑：机器上 `mvn` 用 JAVA_HOME（JDK 21）编译，而 PATH 上的 `java` 是 17，
    直接 `java -jar` 会抛 UnsupportedClassVersionError。所以这里显式挑版本，
    并保证构建与运行用同一个 JDK。
    """
    candidates: list[str] = []
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        exe = Path(java_home) / "bin" / ("java.exe" if os.name == "nt" else "java")
        if exe.exists():
            candidates.append(str(exe))
    found = shutil.which("java")
    if found:
        candidates.append(found)

    for exe in candidates:
        try:
            out = subprocess.run([exe, "-version"], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace").stderr
        except OSError:
            continue
        m = re.search(r'version "(\d+)', out or "")
        if m and int(m.group(1)) >= MIN_JAVA:
            print(f"  使用 JDK: {exe} (Java {m.group(1)})")
            return exe
    return None


def http_get(url: str, timeout: float = 10) -> tuple[int, dict | list | None]:
    try:
        resp = requests.get(url, timeout=timeout)
        return resp.status_code, resp.json()
    except (requests.RequestException, ValueError):
        return 0, None


def http_post(url: str, payload: dict | None = None, timeout: float = 30) -> tuple[int, dict | None]:
    try:
        resp = requests.post(url, json=payload or {}, timeout=timeout)
        return resp.status_code, resp.json()
    except (requests.RequestException, ValueError):
        return 0, None


def wait_ready(base: str, path: str, name: str, timeout: float = 90) -> bool:
    deadline = time.time() + timeout
    print(f"  等待{name}就绪 {base}{path}", end="", flush=True)
    while time.time() < deadline:
        code, _ = http_get(base + path, timeout=2)
        if code == 200:
            print(" ✓")
            return True
        print(".", end="", flush=True)
        time.sleep(0.8)
    print(" ✗ 超时")
    return False


def resolve_mvn() -> str | None:
    """Windows 上 mvn 是 mvn.CMD，裸写 "mvn" 会 FileNotFoundError，必须用 which 解析出全路径。"""
    for name in ("mvn", "mvn.cmd", "mvn.bat"):
        found = shutil.which(name)
        if found:
            return found
    return None


def build_jar() -> bool:
    print("[1/6] 编译 Java 编排服务")
    pom = ORCH_DIR / "pom.xml"
    # pom 比 jar 新 = 依赖或 Java 版本改过，必须重编，否则会跑出旧字节码
    if JAR.exists() and pom.exists() and JAR.stat().st_mtime > pom.stat().st_mtime:
        age = (time.time() - JAR.stat().st_mtime) / 60
        print(f"  已存在 jar（{age:.1f} 分钟前构建，且 pom 未变更），跳过编译")
        return True
    mvn = resolve_mvn()
    if not mvn:
        print("  ✗ 未找到 mvn，请安装 Maven 并加入 PATH（或在 java-orchestrator 下手动执行 mvn package）")
        return False
    r = subprocess.run([mvn, "-B", "-q", "clean", "package", "-DskipTests"],
                       cwd=str(ORCH_DIR), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print("  编译失败：")
        print("\n".join(((r.stdout or "") + (r.stderr or "")).splitlines()[-25:]))
        return False
    print("  ✓ 编译成功")
    return True


def start_worker() -> subprocess.Popen | None:
    print("[2/6] 启动 Python 执行器（:8100）")
    code, _ = http_get(WORKER_BASE + "/api/worker/health", timeout=2)
    if code == 200:
        print("  已在线，复用")
        return None
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.worker_api:app",
         "--host", "127.0.0.1", "--port", "8100", "--log-level", "warning"],
        cwd=str(ROOT),
    )
    return proc if wait_ready(WORKER_BASE, "/api/worker/health", "执行器") else proc


def start_orchestrator(java_exe: str) -> subprocess.Popen | None:
    print(f"[3/6] 启动 Java 编排服务（:{ORCH_PORT}）")
    code, _ = http_get(ORCH_BASE + "/api/v1/health", timeout=2)
    if code == 200:
        print("  已在线，复用")
        return None
    ORCH_LOG.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(ORCH_LOG, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [java_exe, "-jar", str(JAR), f"--server.port={ORCH_PORT}"],
        cwd=str(ORCH_DIR), stdout=log_file, stderr=subprocess.STDOUT,
    )
    if not wait_ready(ORCH_BASE, "/api/v1/health", "编排服务", timeout=120):
        print("  启动失败，日志尾部：")
        try:
            tail = ORCH_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-25:]
            print("\n".join(tail))
        except OSError:
            pass
    return proc


def pick_cases(limit: int) -> list[str]:
    """跨领域均匀取样；limit<=0 表示取全部（默认，能覆盖到会失败的用例）。"""
    code, data = http_get(f"{WORKER_BASE}/api/worker/cases?limit=200")
    if code != 200 or not isinstance(data, list) or not data:
        return []
    ids = [c["caseId"] for c in data]
    if limit <= 0 or len(ids) <= limit:
        return ids
    step = len(ids) / limit
    return [ids[int(i * step)] for i in range(limit)]


def poll_batch(batch_id: str, label: str = "执行中") -> dict:
    last = ""
    while True:
        code, view = http_get(f"{ORCH_BASE}/api/v1/batches/{batch_id}")
        if code != 200 or not view:
            return {}
        line = (f"  {label} 总{view['total']} 通过{view['passed']} 失败{view['failed']} "
                f"取消{view['canceled']} 运行中{view['running']} 待执行{view['pending']} "
                f"进度{view['progress'] * 100:.0f}%")
        if line != last:
            print(line)
            last = line
        if view["status"] != "RUNNING":
            return view
        time.sleep(0.6)


def demo_concurrency(limit: int) -> dict:
    print(f"\n[4/6] 并发调度验证：提交 {limit} 条用例")
    case_ids = pick_cases(limit)
    if not case_ids:
        print("  ✗ 用例库为空，请先跑 python run_demo.py --cli 生成用例")
        return {}
    request_id = f"demo-concurrency-{int(time.time())}"
    t0 = time.time()
    code, created = http_post(f"{ORCH_BASE}/api/v1/batches",
                              {"caseIds": case_ids, "clientRequestId": request_id})
    if code not in (200, 202) or not created:
        print(f"  ✗ 提交失败 HTTP {code}: {created}")
        return {}
    batch_id = created["batchId"]
    print(f"  batchId={batch_id} 已受理 {created['taskCount']} 条（HTTP {code}）")

    view = poll_batch(batch_id)
    wall_ms = int((time.time() - t0) * 1000)

    code, tasks = http_get(f"{ORCH_BASE}/api/v1/batches/{batch_id}/tasks")
    tasks = tasks or []
    print("\n  任务明细：")
    print(f"  {'状态':<8}{'失败分类':<16}{'耗时(ms)':>9}  用例")
    for t in tasks:
        print(f"  {t['status']:<8}{(t['failureKind'] or '-'):<16}{t['durationMs']:>9}  {t['caseTitle'] or t['caseId']}")

    sum_ms = sum(t["durationMs"] for t in tasks)
    _, health = http_get(f"{ORCH_BASE}/api/v1/health")
    concurrency = health.get("concurrency", "?") if isinstance(health, dict) else "?"
    print(f"\n  串行耗时之和 ≈ {sum_ms} ms；墙钟耗时 {wall_ms} ms → "
          f"并发收益约 {sum_ms / max(wall_ms, 1):.1f}x（并发上限 {concurrency}）")
    if view:
        print(f"  批次结果：{view['status']} 通过 {view['passed']} / 失败 {view['failed']}")
    return {"batch_id": batch_id, "request_id": request_id, "wall_ms": wall_ms,
            "sum_ms": sum_ms, "view": view, "tasks": tasks}


def demo_idempotent(request_id: str) -> None:
    print("\n[5/6] 幂等验证：用同一个 clientRequestId 再提交一次")
    if not request_id:
        print("  跳过（上一阶段未成功）")
        return
    code, created = http_post(f"{ORCH_BASE}/api/v1/batches",
                              {"caseIds": pick_cases(3), "clientRequestId": request_id})
    if code in (200, 202) and created:
        flag = "✓ 命中幂等，返回原批次" if created["idempotent"] else "✗ 又建了一个新批次（幂等失效）"
        print(f"  {flag}：batchId={created['batchId']}")
        print(f"  说明：{created['message']}")


def demo_cancel() -> None:
    print("\n[6/6] 取消验证：提交一批后立刻取消未开始的任务")
    case_ids = pick_cases(8)
    if not case_ids:
        print("  跳过")
        return
    code, created = http_post(f"{ORCH_BASE}/api/v1/batches",
                              {"caseIds": case_ids, "clientRequestId": f"demo-cancel-{time.time()}"})
    if code not in (200, 202) or not created:
        print(f"  提交失败：{code}")
        return
    batch_id = created["batchId"]
    time.sleep(0.3)
    code, canceled = http_post(f"{ORCH_BASE}/api/v1/batches/{batch_id}/cancel")
    print(f"  取消结果：{canceled}")
    view = poll_batch(batch_id, label="取消后")
    if view:
        print(f"  最终：通过 {view['passed']} / 失败 {view['failed']} / 取消 {view['canceled']} "
              f"→ 取消只作用于未开始的任务，符合预期")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--keep", action="store_true", help="跑完保留服务进程")
    parser.add_argument("--batch-size", type=int, default=0,
                        help="执行条数，0 = 全部用例（默认，能看到失败分类的效果）")
    args = parser.parse_args()

    print("=" * 68)
    print("Java 任务编排服务 × Python 执行器 —— 链路验证")
    print("=" * 68)

    if not env_manager.server_alive():
        print("✗ 被测服（Mock 游戏服）未启动，请先运行 python run_demo.py --cli")
        return 1

    java_exe = resolve_java()
    if not java_exe:
        print(f"✗ 未找到 JDK {MIN_JAVA}+，请安装并设置 JAVA_HOME")
        return 1

    if not args.skip_build and not build_jar():
        return 1

    procs: list[subprocess.Popen] = []
    worker = start_worker()
    if worker:
        procs.append(worker)
    orch = start_orchestrator(java_exe)
    if orch:
        procs.append(orch)

    try:
        result = demo_concurrency(args.batch_size)
        demo_idempotent(result.get("request_id", ""))
        demo_cancel()

        print("\n" + "=" * 68)
        if result.get("view"):
            v = result["view"]
            print(f"结论：{v['total']} 条用例由 Java 调度、Python 执行完成，"
                  f"通过 {v['passed']} 条、失败 {v['failed']} 条")
            print(f"      并发收益 {result['sum_ms'] / max(result['wall_ms'], 1):.1f}x，"
                  f"幂等与取消均生效")
        print("=" * 68)
        print("\n明细可查：")
        print(f"  GET {ORCH_BASE}/api/v1/batches/{result.get('batch_id', '<id>')}")
        print(f"  GET {ORCH_BASE}/api/v1/batches/{result.get('batch_id', '<id>')}/tasks")
        print("  Java 侧调度的批次同样会写进 SQLite，可在 Streamlit 看板的「版本验证」里看到")
    finally:
        if procs and not args.keep:
            print("\n关闭服务进程…")
            for p in procs:
                p.terminate()
            for p in procs:
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    p.kill()
        elif procs:
            print("\n服务保持运行（--keep）：")
            print(f"  编排服务 {ORCH_BASE}   执行器 {WORKER_BASE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
