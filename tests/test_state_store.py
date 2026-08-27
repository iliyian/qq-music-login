"""session_persistence 存取与探活判定单测（纯逻辑，不碰网络）。"""

import json

import pytest

from src.session_persistence import (
    CookieStore,
    RunRecord,
    extract_target_cookies,
    has_valid_login_payload,
    judge_probe_response,
    record_last_run,
)


# ─── CookieStore ──────────────────────────────────────────


def test_cookie_store_roundtrip(tmp_path):
    store = CookieStore(tmp_path / "session.json")
    assert not store.exists()
    cookies = [
        {"name": "qm_keyst", "value": "Q_H_L_abc", "domain": ".qq.com", "path": "/"},
        {"name": "uin", "value": "o12345", "domain": ".qq.com", "path": "/"},
        {"name": "psrf_qqaccess_token", "value": "tok", "domain": ".qq.com", "path": "/"},
    ]
    store.save(cookies)
    assert store.exists()
    loaded = store.load()
    assert loaded is not None
    assert len(loaded["cookies"]) == 3
    assert loaded["cookies"][0]["name"] == "qm_keyst"
    assert loaded["saved_at"] > 0
    assert [c["name"] for c in store.load_cookies()] == ["qm_keyst", "uin", "psrf_qqaccess_token"]


def test_cookie_store_missing_and_corrupt(tmp_path):
    store = CookieStore(tmp_path / "nope.json")
    assert store.load() is None
    assert store.load_cookies() == []

    bad = tmp_path / "bad.json"
    bad.write_text("{{{not json")
    store2 = CookieStore(bad)
    assert store2.load() is None

    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"cookies": []}))
    assert CookieStore(empty).load() is None


def test_cookie_store_invalidate(tmp_path):
    fp = tmp_path / "session.json"
    store = CookieStore(fp)
    store.save([{"name": "uin", "value": "1", "domain": ".qq.com", "path": "/"}])
    store.invalidate()
    assert not store.exists()
    store.invalidate()  # 幂等


# ─── cookie 抽取与登录判定 ──────────────────────────────────


def test_extract_target_cookies_filters():
    cookies = [
        {"name": "qm_keyst", "value": "k1"},
        {"name": "random_other", "value": "x"},
        {"name": "uin", "value": "o123"},
        {"name": "psrf_qqaccess_token", "value": "t"},
        {"name": "broken"},
    ]
    got = extract_target_cookies(cookies)
    assert got == {"qm_keyst": "k1", "uin": "o123", "psrf_qqaccess_token": "t"}


def test_has_valid_login_payload():
    assert has_valid_login_payload({"qqmusic_key": "abc"})
    assert has_valid_login_payload({"qm_keyst": "abc"})
    assert not has_valid_login_payload({"uin": "o123"})
    assert not has_valid_login_payload({})


# ─── 探活判定 ──────────────────────────────────────────────


def test_judge_probe_response_valid():
    ok = {
        "code": 0,
        "req": {"code": 0, "data": {"uin": 123456, "nick": "someone"}},
    }
    assert judge_probe_response(ok) is True


def test_judge_probe_response_valid_by_key():
    ok = {
        "code": 0,
        "req": {"code": 0, "data": {"uin": 0, "qqmusic_key": "Q_H_L_xxx"}},
    }
    assert judge_probe_response(ok) is True


@pytest.mark.parametrize(
    "bad",
    [
        None,
        {},
        {"code": 1001, "req": {"code": 1001, "data": {}}},  # 未登录
        {"code": 0, "req": {"code": 2001, "data": {}}},      # 子请求失败
        {"code": 0, "req": {"code": 0, "data": {"uin": 0}}},  # uin=0 且无 key
        {"code": 0, "req": {"code": 0, "data": {"uin": ""}}},
        {"code": 0, "req": {"code": 0}},                      # 无 data
        "not-a-dict",
    ],
)
def test_judge_probe_response_invalid(bad):
    assert judge_probe_response(bad) is False


# ─── last_run.json ─────────────────────────────────────────


def test_record_last_run(tmp_path):
    path = tmp_path / "last_run.json"
    record_last_run(RunRecord(success=False, reason="probe failed", source="silent_refresh"), path)
    record_last_run(RunRecord(success=True, source="browser_login"), path)
    data = json.loads(path.read_text())
    assert len(data) == 2
    assert data[0]["success"] is False
    assert data[0]["source"] == "silent_refresh"
    assert data[1]["reason"] == ""
    assert "iso_time" in data[1]


def test_record_last_run_corrupt_file(tmp_path):
    path = tmp_path / "last_run.json"
    path.write_text("garbage[")
    record_last_run(RunRecord(success=True), path)
    data = json.loads(path.read_text())
    assert len(data) == 1