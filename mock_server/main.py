"""Mock 游戏服（HTTP 层）。启动：python -m mock_server.main

只做协议适配，全部业务逻辑在 game_state.GameWorld 中，便于单测与复用。
"""
from __future__ import annotations

from typing import Any

from fastapi import Body, FastAPI, Header

from app import config
from mock_server.game_state import GameWorld

world = GameWorld(reward_delay=config.MOCK_REWARD_DELAY, bugs=tuple(config.MOCK_BUGS))

app = FastAPI(
    title="Mock Game Server（AI 测试平台被测对象）",
    description="模拟抽卡 / 关卡 / 异步发奖 / GM 指令的游戏服务端",
    version="1.0.0",
)


def _token(authorization: str | None) -> str:
    if not authorization:
        return ""
    return authorization.replace("Bearer ", "").strip()


@app.get("/")
def root() -> dict[str, Any]:
    return {
        "service": "mock-game-server",
        "bugs": sorted(world.bugs),
        "reward_delay": world.reward_delay,
        "players": len(world.players),
    }


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"code": 0, "msg": "ok", "data": {"status": "healthy"}}


@app.post("/api/login")
def login(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    return world.login(str(payload.get("account", "")))


@app.get("/api/bag")
def bag(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    return world.bag_view(_token(authorization))


@app.post("/api/gacha/draw")
def gacha_draw(payload: dict[str, Any] = Body(default={}),
               authorization: str | None = Header(default=None)) -> dict[str, Any]:
    return world.gacha_draw(
        _token(authorization),
        str(payload.get("pool_id", "")),
        payload.get("times", 0),
    )


@app.post("/api/stage/enter")
def stage_enter(payload: dict[str, Any] = Body(default={}),
                authorization: str | None = Header(default=None)) -> dict[str, Any]:
    return world.stage_enter(_token(authorization), payload.get("stage_id", 0))


@app.post("/api/stage/settle")
def stage_settle(payload: dict[str, Any] = Body(default={}),
                 authorization: str | None = Header(default=None)) -> dict[str, Any]:
    return world.stage_settle(
        _token(authorization),
        str(payload.get("battle_id", "")),
        payload.get("win", False),
    )


@app.get("/api/reward/{reward_id}")
def reward_query(reward_id: str,
                 authorization: str | None = Header(default=None)) -> dict[str, Any]:
    return world.reward_query(_token(authorization), reward_id)


@app.post("/api/reward/claim")
def reward_claim(payload: dict[str, Any] = Body(default={}),
                 authorization: str | None = Header(default=None)) -> dict[str, Any]:
    return world.reward_claim(_token(authorization), str(payload.get("reward_id", "")))


@app.post("/api/admin/pity")
def admin_pity(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    return world.admin_pity(
        str(payload.get("account", "")),
        str(payload.get("pool_id", "")),
        payload.get("pity", 0),
    )


@app.post("/api/admin/grant")
def admin_grant(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    return world.admin_grant(
        str(payload.get("account", "")),
        payload.get("gems"),
        payload.get("stamina"),
    )


@app.post("/api/admin/bag_fill")
def admin_bag_fill(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    return world.admin_bag_fill(
        str(payload.get("account", "")),
        payload.get("count", 200),
    )


@app.post("/api/admin/reset")
def admin_reset(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    return world.admin_reset(str(payload.get("account", "")))


@app.post("/api/admin/config")
def admin_config(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    return world.admin_config(payload.get("reward_delay"), payload.get("bugs"))


@app.post("/api/telemetry/stage")
def telemetry(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    return world.report_telemetry(payload)


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=config.MOCK_PORT, log_level="warning")
