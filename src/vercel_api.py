"""Vercel 环境变量更新（沿用旧版 _upsert_env_var / _trigger_redeploy 逻辑）。"""

from __future__ import annotations

import requests as http_requests

from .config import VERCEL_API


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _proxies(proxy: str | None) -> dict | None:
    return {"http": proxy, "https": proxy} if proxy else None


def _get_env_vars(token: str, project_id: str, proxy: str | None = None) -> list[dict]:
    """获取项目当前所有环境变量"""
    resp = http_requests.get(
        f"{VERCEL_API}/v9/projects/{project_id}/env",
        headers=_headers(token),
        proxies=_proxies(proxy),
    )
    resp.raise_for_status()
    return resp.json().get("envs", [])


def _upsert_env_var(token: str, project_id: str, key: str, value: str, proxy: str | None = None):
    """创建或更新单个环境变量（覆盖所有target: production/preview/development）"""
    headers = _headers(token)
    proxies = _proxies(proxy)
    envs = _get_env_vars(token, project_id, proxy=proxy)

    existing = [e for e in envs if e["key"] == key]

    if existing:
        env_id = existing[0]["id"]
        resp = http_requests.patch(
            f"{VERCEL_API}/v9/projects/{project_id}/env/{env_id}",
            headers=headers,
            json={
                "value": value,
                "target": ["production", "preview", "development"],
                "type": "encrypted",
            },
            proxies=proxies,
        )
    else:
        resp = http_requests.post(
            f"{VERCEL_API}/v10/projects/{project_id}/env",
            headers=headers,
            json={
                "key": key,
                "value": value,
                "target": ["production", "preview", "development"],
                "type": "encrypted",
            },
            proxies=proxies,
        )

    resp.raise_for_status()
    action = "更新" if existing else "创建"
    print(f"  {action} {key} 成功")


def _trigger_redeploy(token: str, project_id: str, proxy: str | None = None):
    """获取最近一次production部署并触发重新部署"""
    headers = _headers(token)
    proxies = _proxies(proxy)

    # 获取最近的 production deployment
    resp = http_requests.get(
        f"{VERCEL_API}/v6/deployments",
        headers=headers,
        params={"projectId": project_id, "target": "production", "limit": 1},
        proxies=proxies,
    )
    resp.raise_for_status()
    deployments = resp.json().get("deployments", [])

    if not deployments:
        print("  警告：未找到production部署，跳过重新部署")
        return

    deploy_id = deployments[0]["uid"]
    name = deployments[0].get("name", "?")

    resp = http_requests.post(
        f"{VERCEL_API}/v13/deployments",
        headers=headers,
        json={
            "name": name,
            "deploymentId": deploy_id,
            "target": "production",
        },
        proxies=proxies,
    )
    resp.raise_for_status()
    new_url = resp.json().get("url", "")
    print(f"  已触发重新部署: {new_url}")


def update_vercel(token: str, project_id: str, uin: str, qqmusic_key: str, proxy: str | None = None):
    """更新Vercel环境变量并触发重新部署"""
    print("\n[Vercel] 更新环境变量...")
    if proxy:
        print(f"[Vercel] 使用代理: {proxy}")
    _upsert_env_var(token, project_id, "QQ_UIN", uin, proxy=proxy)
    _upsert_env_var(token, project_id, "QQ_MUSIC_KEY", qqmusic_key, proxy=proxy)

    print("[Vercel] 触发重新部署...")
    _trigger_redeploy(token, project_id, proxy=proxy)
