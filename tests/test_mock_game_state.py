"""被测游戏服的核心业务规则单测（不启 HTTP，直接对拍逻辑）。"""
from __future__ import annotations

from mock_server.game_state import BAG_CAPACITY, POOLS, GameWorld


def _world(bugs: tuple[str, ...] = ()) -> tuple[GameWorld, str]:
    world = GameWorld(reward_delay=0.0, bugs=bugs)
    token = world.login("qa_unit")["data"]["token"]
    return world, token


def test_login_creates_player() -> None:
    world, token = _world()
    assert token.startswith("tk_")
    assert world.bag_view(token)["data"]["gems"] == 8000


def test_gacha_deducts_exact_cost() -> None:
    world, token = _world()
    before = world.bag_view(token)["data"]["gems"]
    result = world.gacha_draw(token, "pool_standard", 1)
    assert result["code"] == 0
    assert result["data"]["gems"] == before - POOLS["pool_standard"]["cost"]


def test_gacha_times_out_of_range() -> None:
    world, token = _world()
    assert world.gacha_draw(token, "pool_standard", 0)["code"] == 1001
    assert world.gacha_draw(token, "pool_standard", 11)["code"] == 1001


def test_gacha_insufficient_gems_keeps_balance() -> None:
    world, token = _world()
    world.admin_grant("qa_unit", gems=10)
    assert world.gacha_draw(token, "pool_standard", 1)["code"] == 1003
    assert world.bag_view(token)["data"]["gems"] == 10


def test_pity_works_when_no_bug() -> None:
    world, token = _world()
    limit = POOLS["pool_standard"]["pity_limit"]
    world.admin_pity("qa_unit", "pool_standard", limit - 1)
    result = world.gacha_draw(token, "pool_standard", 1)
    assert result["data"]["items"][0]["rarity"] == "SSR"


def test_pity_off_by_one_bug_reproduces() -> None:
    """注入的缺陷必须能稳定复现，否则自动化抓不到。"""
    world, token = _world(bugs=("gacha_pity_off_by_one",))
    limit = POOLS["pool_standard"]["pity_limit"]
    world.admin_pity("qa_unit", "pool_standard", limit - 1)
    result = world.gacha_draw(token, "pool_standard", 1)
    assert result["data"]["items"][0]["rarity"] != "SSR"


def test_bag_overflow_goes_to_mail() -> None:
    world, token = _world()
    world.admin_bag_fill("qa_unit", BAG_CAPACITY)
    result = world.gacha_draw(token, "pool_standard", 1)
    assert result["code"] == 0
    assert result["data"]["mail_delivered"] is True


def test_reward_flow_and_item_count() -> None:
    world, token = _world()
    battle = world.stage_enter(token, 1)["data"]["battle_id"]
    reward_id = world.stage_settle(token, battle, True)["data"]["reward_id"]
    body = world.reward_query(token, reward_id)["data"]
    assert body["status"] == "claimable"
    assert len(body["items"]) > 0
    claim = world.reward_claim(token, reward_id)
    assert claim["code"] == 0 and claim["data"]["granted"]


def test_lose_gives_empty_reward() -> None:
    world, token = _world()
    battle = world.stage_enter(token, 1)["data"]["battle_id"]
    reward_id = world.stage_settle(token, battle, False)["data"]["reward_id"]
    assert world.reward_query(token, reward_id)["data"]["items"] == []


def test_duplicate_settle_rejected() -> None:
    world, token = _world()
    battle = world.stage_enter(token, 1)["data"]["battle_id"]
    world.stage_settle(token, battle, True)
    assert world.stage_settle(token, battle, True)["code"] == 1005


def test_double_claim_ok_without_bug() -> None:
    world, token = _world()
    battle = world.stage_enter(token, 1)["data"]["battle_id"]
    reward_id = world.stage_settle(token, battle, True)["data"]["reward_id"]
    world.reward_claim(token, reward_id)
    assert world.reward_claim(token, reward_id)["code"] == 1005


def test_double_claim_bug_grants_twice() -> None:
    world, token = _world(bugs=("reward_double_claim",))
    battle = world.stage_enter(token, 1)["data"]["battle_id"]
    reward_id = world.stage_settle(token, battle, True)["data"]["reward_id"]
    first = world.reward_claim(token, reward_id)["data"]["granted"]
    second = world.reward_claim(token, reward_id)["data"]["granted"]
    assert first and second, "缺陷模式下第二次领取仍应发奖（这就是待抓的 bug）"


def test_stamina_lack_blocks_enter() -> None:
    world, token = _world()
    world.admin_grant("qa_unit", stamina=0)
    assert world.stage_enter(token, 1)["code"] == 1003


def test_unauthorized_access() -> None:
    world, _ = _world()
    assert world.bag_view("bad_token")["code"] == 1002


def test_reset_clears_state() -> None:
    world, token = _world()
    world.gacha_draw(token, "pool_standard", 10)
    world.admin_reset("qa_unit")
    data = world.bag_view(token)["data"]
    assert data["gems"] == 8000 and data["items"] == []
