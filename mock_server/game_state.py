"""被测试的「游戏服务端」核心逻辑。

刻意做成纯 Python（不依赖 HTTP 框架），这样保底、背包、幂等这些核心规则可以被
单元测试直接覆盖，也方便在 UI 里做双人对拍。

内置 3 个真实项目里高频出现的缺陷，用开关注入（默认开启 2 个），
用来证明「自动化是真的能抓到 bug」，不是摆设：
    1. gacha_pity_off_by_one —— 保底计数差一（第 91 抽才触发，需求要求第 90 抽）；
    2. reward_double_claim  —— 奖励领取幂等失效，重复领奖重复发；
    3. stamina_cost_mismatch —— 进关体力扣除比配置多 1 点。
"""
from __future__ import annotations

import random
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.api_catalog import (
    ERR_NOT_FOUND,
    ERR_OK,
    ERR_PARAM,
    ERR_RESOURCE_LACK,
    ERR_STATE_CONFLICT,
    ERR_UNAUTHORIZED,
)

BAG_CAPACITY = 200

POOLS: dict[str, dict[str, Any]] = {
    "pool_standard": {
        "name": "常驻卡池",
        "cost": 160,
        "pity_limit": 90,
        "rates": {"SSR": 0.006, "SR": 0.051, "R": 0.943},
    },
    "pool_featured": {
        "name": "限定卡池",
        "cost": 160,
        "pity_limit": 90,
        "rates": {"SSR": 0.006, "SR": 0.051, "R": 0.943},
    },
}

RARITY_ITEM = {"SSR": "hero_ssr_01", "SR": "hero_sr_01", "R": "mat_r_01"}


def stage_table() -> dict[int, dict[str, Any]]:
    stages: dict[int, dict[str, Any]] = {}
    for i in range(1, 16):
        stages[i] = {
            "stage_id": i,
            "name": f"主线 1-{i}",
            "stamina": 10,
            "recommend_power": 1000 + i * 350,
            "clear_reward": [
                {"item_id": "mat_gold", "count": 100 + i * 10},
                {"item_id": "exp_potion", "count": 2},
            ],
        }
    return stages


STAGES = stage_table()


def ok(data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"code": ERR_OK, "msg": "ok", "data": data or {}}


def err(code: int, msg: str) -> dict[str, Any]:
    return {"code": code, "msg": msg, "data": {}}


@dataclass
class Player:
    account: str
    player_id: str
    token: str
    gems: int = 8000
    stamina: int = 120
    bag: dict[str, int] = field(default_factory=dict)
    pity: dict[str, int] = field(default_factory=dict)
    mail: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Battle:
    battle_id: str
    account: str
    stage_id: int
    settled: bool = False


@dataclass
class Reward:
    reward_id: str
    account: str
    items: list[dict[str, Any]]
    ready_at: float
    claimed: bool = False


class GameWorld:
    def __init__(self, reward_delay: float = 1.5, bugs: tuple[str, ...] | list[str] = ()) -> None:
        self._lock = threading.RLock()
        self.reward_delay = float(reward_delay)
        self.bugs = set(bugs)
        self.players: dict[str, Player] = {}
        self.battles: dict[str, Battle] = {}
        self.rewards: dict[str, Reward] = {}
        self.telemetry: list[dict[str, Any]] = []
        self._clock = 0.0  # 逻辑时钟（秒），避免依赖真实时间便于测试

    # ------------------------------------------------------------ 内部工具
    def tick(self, seconds: float = 0.0) -> float:
        """推进逻辑时间；真实服务里请用 time.time()，这里为保证可测试性自维护。"""
        import time

        self._clock = time.time() + seconds
        return self._clock

    def now(self) -> float:
        import time

        return time.time()

    def _player(self, account: str) -> Player | None:
        return self.players.get(account)

    def _authed(self, token: str) -> Player | None:
        for p in self.players.values():
            if p.token == token:
                return p
        return None

    def _add_item(self, player: Player, item_id: str, count: int) -> bool:
        """返回 True 表示走邮件补发（背包已满且是新道具）。"""
        if item_id in player.bag:
            player.bag[item_id] += count
            return False
        if len(player.bag) >= BAG_CAPACITY:
            player.mail.append({"item_id": item_id, "count": count})
            return True
        player.bag[item_id] = count
        return False

    # ------------------------------------------------------------ 登录
    def login(self, account: str) -> dict[str, Any]:
        if not account or not account.strip():
            return err(ERR_PARAM, "account 不能为空")
        with self._lock:
            p = self.players.get(account)
            if not p:
                p = Player(
                    account=account,
                    player_id="pid_" + uuid.uuid4().hex[:10],
                    token="tk_" + uuid.uuid4().hex[:24],
                )
                self.players[account] = p
            return ok({
                "token": p.token,
                "player_id": p.player_id,
                "account": p.account,
                "gems": p.gems,
                "stamina": p.stamina,
            })

    # ------------------------------------------------------------ 背包
    def bag_view(self, token: str) -> dict[str, Any]:
        with self._lock:
            p = self._authed(token)
            if not p:
                return err(ERR_UNAUTHORIZED, "登录态失效")
            return ok({
                "gems": p.gems,
                "stamina": p.stamina,
                "items": [{"item_id": k, "count": v} for k, v in p.bag.items()],
                "capacity": BAG_CAPACITY,
                "used": len(p.bag),
                "mail_count": len(p.mail),
            })

    # ------------------------------------------------------------ 抽卡
    def gacha_draw(self, token: str, pool_id: str, times: Any) -> dict[str, Any]:
        with self._lock:
            p = self._authed(token)
            if not p:
                return err(ERR_UNAUTHORIZED, "登录态失效")
            try:
                times_i = int(times)
            except (TypeError, ValueError):
                return err(ERR_PARAM, "times 必须是整数")
            if times_i < 1 or times_i > 10:
                return err(ERR_PARAM, "times 必须在 1~10 之间")
            pool = POOLS.get(pool_id)
            if not pool:
                return err(ERR_NOT_FOUND, f"卡池不存在: {pool_id}")

            cost = pool["cost"] * times_i
            if p.gems < cost:
                return err(ERR_RESOURCE_LACK, f"钻石不足，需要 {cost}")

            limit = pool["pity_limit"]
            # 缺陷注入：保底差一，需求是第 limit 抽必出，实际第 limit+1 抽才出
            need = limit + (1 if "gacha_pity_off_by_one" in self.bugs else 0)
            pity_now = p.pity.get(pool_id, 0)

            items: list[dict[str, Any]] = []
            mail_delivered = False
            for i in range(times_i):
                pity_now += 1
                if pity_now >= need:
                    rarity = "SSR"
                    pity_now = 0
                elif pity_now >= limit:
                    # 应触发保底但被缺陷挡住：为让缺陷稳定复现，强制非 SSR
                    rarity = "R"
                else:
                    rarity = self._roll(pool["rates"], p.account, pool_id, pity_now)
                    if rarity == "SSR":
                        pity_now = 0
                item_id = RARITY_ITEM[rarity]
                items.append({"item_id": item_id, "rarity": rarity})
                if self._add_item(p, item_id, 1):
                    mail_delivered = True

            p.gems -= cost
            p.pity[pool_id] = pity_now
            return ok({
                "items": items,
                "gems": p.gems,
                "pity": pity_now,
                "pity_limit": limit,
                "mail_delivered": mail_delivered,
            })

    @staticmethod
    def _roll(rates: dict[str, float], account: str, pool_id: str, pity: int) -> str:
        """确定性随机：同一账号同一保底进度结果可复现，方便复现缺陷。"""
        rnd = random.Random(f"{account}|{pool_id}|{pity}")
        r = rnd.random()
        acc = 0.0
        for rarity, prob in rates.items():
            acc += prob
            if r <= acc:
                return rarity
        return "R"

    # ------------------------------------------------------------ 关卡
    def stage_enter(self, token: str, stage_id: Any) -> dict[str, Any]:
        with self._lock:
            p = self._authed(token)
            if not p:
                return err(ERR_UNAUTHORIZED, "登录态失效")
            try:
                sid = int(stage_id)
            except (TypeError, ValueError):
                return err(ERR_PARAM, "stage_id 必须是整数")
            stage = STAGES.get(sid)
            if not stage:
                return err(ERR_PARAM, f"关卡不存在: {sid}")
            cost = stage["stamina"] + (1 if "stamina_cost_mismatch" in self.bugs else 0)
            if p.stamina < cost:
                return err(ERR_RESOURCE_LACK, f"体力不足，需要 {cost}")
            p.stamina -= cost
            battle_id = "bt_" + uuid.uuid4().hex[:12]
            self.battles[battle_id] = Battle(battle_id, p.account, sid)
            return ok({"battle_id": battle_id, "stage_id": sid,
                       "stamina": p.stamina, "stamina_cost": cost})

    def stage_settle(self, token: str, battle_id: str, win: Any) -> dict[str, Any]:
        with self._lock:
            p = self._authed(token)
            if not p:
                return err(ERR_UNAUTHORIZED, "登录态失效")
            battle = self.battles.get(battle_id or "")
            if not battle or battle.account != p.account:
                return err(ERR_NOT_FOUND, "战斗不存在")
            if battle.settled:
                return err(ERR_STATE_CONFLICT, "该战斗已结算，不能重复结算")
            battle.settled = True
            win_flag = win if isinstance(win, bool) else str(win).lower() in ("1", "true", "yes")
            items = list(STAGES[battle.stage_id]["clear_reward"]) if win_flag else []
            reward_id = "rw_" + uuid.uuid4().hex[:12]
            self.rewards[reward_id] = Reward(
                reward_id=reward_id,
                account=p.account,
                items=items,
                ready_at=self.now() + self.reward_delay,
            )
            return ok({"reward_id": reward_id, "status": "pending", "win": win_flag})

    def reward_query(self, token: str, reward_id: str) -> dict[str, Any]:
        with self._lock:
            p = self._authed(token)
            if not p:
                return err(ERR_UNAUTHORIZED, "登录态失效")
            rw = self.rewards.get(reward_id or "")
            if not rw or rw.account != p.account:
                return err(ERR_NOT_FOUND, "奖励单不存在")
            if rw.claimed:
                status = "claimed"
            elif self.now() >= rw.ready_at:
                status = "claimable"
            else:
                status = "pending"
            return ok({
                "reward_id": rw.reward_id,
                "status": status,
                "items": rw.items,
                "remain_ms": max(0, int((rw.ready_at - self.now()) * 1000)),
            })

    def reward_claim(self, token: str, reward_id: str) -> dict[str, Any]:
        with self._lock:
            p = self._authed(token)
            if not p:
                return err(ERR_UNAUTHORIZED, "登录态失效")
            rw = self.rewards.get(reward_id or "")
            if not rw or rw.account != p.account:
                return err(ERR_NOT_FOUND, "奖励单不存在")
            if self.now() < rw.ready_at:
                return err(ERR_STATE_CONFLICT, "奖励尚未到账，请稍后重试")
            if rw.claimed and "reward_double_claim" not in self.bugs:
                return err(ERR_STATE_CONFLICT, "奖励已领取，不能重复领取")

            granted = []
            for item in rw.items:
                self._add_item(p, item["item_id"], int(item["count"]))
                granted.append({"item_id": item["item_id"], "count": int(item["count"])})
            rw.claimed = True
            return ok({"granted": granted, "already_claimed": len(granted) == 0})

    # ------------------------------------------------------------ GM 指令
    def admin_pity(self, account: str, pool_id: str, pity: Any) -> dict[str, Any]:
        with self._lock:
            p = self._player(account)
            if not p:
                return err(ERR_NOT_FOUND, "账号不存在，请先登录")
            if pool_id not in POOLS:
                return err(ERR_NOT_FOUND, f"卡池不存在: {pool_id}")
            try:
                v = int(pity)
            except (TypeError, ValueError):
                return err(ERR_PARAM, "pity 必须是整数")
            p.pity[pool_id] = max(0, min(v, POOLS[pool_id]["pity_limit"]))
            return ok({"account": account, "pool_id": pool_id, "pity": p.pity[pool_id]})

    def admin_grant(self, account: str, gems: Any = None, stamina: Any = None) -> dict[str, Any]:
        with self._lock:
            p = self._player(account)
            if not p:
                return err(ERR_NOT_FOUND, "账号不存在，请先登录")
            if gems is not None:
                try:
                    p.gems = max(0, int(gems))
                except (TypeError, ValueError):
                    return err(ERR_PARAM, "gems 必须是整数")
            if stamina is not None:
                try:
                    p.stamina = max(0, int(stamina))
                except (TypeError, ValueError):
                    return err(ERR_PARAM, "stamina 必须是整数")
            return ok({"account": account, "gems": p.gems, "stamina": p.stamina})

    def admin_bag_fill(self, account: str, count: Any = BAG_CAPACITY) -> dict[str, Any]:
        with self._lock:
            p = self._player(account)
            if not p:
                return err(ERR_NOT_FOUND, "账号不存在，请先登录")
            try:
                target = max(0, min(int(count), BAG_CAPACITY))
            except (TypeError, ValueError):
                return err(ERR_PARAM, "count 必须是整数")
            i = 0
            while len(p.bag) < target:
                p.bag[f"filler_{i}"] = 1
                i += 1
                if i > BAG_CAPACITY * 2:
                    break
            return ok({"used": len(p.bag), "capacity": BAG_CAPACITY})

    def admin_reset(self, account: str) -> dict[str, Any]:
        with self._lock:
            p = self.players.get(account)
            if not p:
                return ok({"account": account, "reset": False})
            p.gems = 8000
            p.stamina = 120
            p.bag = {}
            p.pity = {}
            p.mail = []
            for bid in [b for b, v in self.battles.items() if v.account == account]:
                self.battles.pop(bid, None)
            for rid in [r for r, v in self.rewards.items() if v.account == account]:
                self.rewards.pop(rid, None)
            return ok({"account": account, "reset": True, "gems": p.gems, "stamina": p.stamina})

    def admin_config(self, reward_delay: Any = None, bugs: Any = None) -> dict[str, Any]:
        with self._lock:
            if reward_delay is not None:
                self.reward_delay = max(0.0, float(reward_delay))
            if bugs is not None:
                if isinstance(bugs, str):
                    self.bugs = {b.strip() for b in bugs.split(",") if b.strip()}
                elif isinstance(bugs, list):
                    self.bugs = set(bugs)
            return ok({"reward_delay": self.reward_delay, "bugs": sorted(self.bugs)})

    # ------------------------------------------------------------ 埋点
    def report_telemetry(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.telemetry.append(payload)
            return ok({"received": len(self.telemetry)})
