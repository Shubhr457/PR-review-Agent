#!/usr/bin/env python
"""
scratch/load_test.py

Concurrency stress-testing utility for the PR Review Agent.
Generates concurrent webhook payloads with correct HMAC signatures to test
endpoint stability, latency, and rate limiting.
"""

import argparse
import asyncio
import hashlib
import hmac
import time
from typing import Any, Dict, List
import httpx

# A minimal, valid GitHub webhook payload for a pull request opened event
PAYLOAD_TEMPLATE = {
    "action": "opened",
    "number": 42,
    "pull_request": {
        "number": 42,
        "title": "feat: load test pull request",
        "state": "open",
        "body": "This is a test PR.",
        "user": {"login": "octocat", "id": 1},
        "head": {
            "ref": "feature-branch",
            "sha": "abc123_load_test_commit_sha",
            "repo": {
                "id": 1,
                "name": "Hello-World",
                "full_name": "octocat/Hello-World",
                "clone_url": "https://github.com/octocat/Hello-World.git",
                "default_branch": "main",
            },
        },
        "base": {
            "ref": "main",
            "sha": "def456",
            "repo": {
                "id": 1,
                "name": "Hello-World",
                "full_name": "octocat/Hello-World",
                "clone_url": "https://github.com/octocat/Hello-World.git",
                "default_branch": "main",
            },
        },
        "url": "https://api.github.com/repos/octocat/Hello-World/pulls/42",
        "html_url": "https://github.com/octocat/Hello-World/pull/42",
        "diff_url": "https://github.com/octocat/Hello-World/pull/42.diff",
        "additions": 10,
        "deletions": 2,
        "changed_files": 3,
    },
    "repository": {
        "id": 1,
        "name": "Hello-World",
        "full_name": "octocat/Hello-World",
        "clone_url": "https://github.com/octocat/Hello-World.git",
        "default_branch": "main",
    },
    "sender": {"login": "octocat", "id": 1},
}

def generate_signature(body: bytes, secret: str) -> str:
    """Generate the GitHub HMAC-SHA256 signature for a payload body."""
    mac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"

async def send_webhook(client: httpx.AsyncClient, url: str, payload: Dict[str, Any], secret: str, request_id: int) -> Dict[str, Any]:
    """Send a single webhook request and measure execution latency."""
    import json
    body_bytes = json.dumps(payload).encode("utf-8")
    signature = generate_signature(body_bytes, secret)
    
    headers = {
        "Content-Type": "application/json",
        "X-GitHub-Event": "pull_request",
        "X-Hub-Signature-256": signature,
    }
    
    start_time = time.perf_counter()
    try:
        response = await client.post(url, content=body_bytes, headers=headers)
        latency = time.perf_counter() - start_time
        return {
            "id": request_id,
            "status_code": response.status_code,
            "latency_sec": latency,
            "success": response.status_code in (200, 202),
            "response_text": response.text[:100]
        }
    except Exception as exc:
        latency = time.perf_counter() - start_time
        return {
            "id": request_id,
            "status_code": 0,
            "latency_sec": latency,
            "success": False,
            "response_text": str(exc)
        }

async def run_load_test(url: str, concurrency: int, secret: str):
    """Run concurrent webhooks using httpx AsyncClient."""
    print(f"🚀 Starting load test targeting: {url}")
    print(f"👥 Concurrency: {concurrency} requests")
    print(f"🔑 HMAC Secret: {secret}\n")
    
    # We disable timeout to measure raw server response duration
    timeout = httpx.Timeout(20.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        tasks = [
            send_webhook(client, url, PAYLOAD_TEMPLATE, secret, i + 1)
            for i in range(concurrency)
        ]
        
        start_test_time = time.perf_counter()
        results: List[Dict[str, Any]] = await asyncio.gather(*tasks)
        total_test_time = time.perf_counter() - start_test_time
        
    print("📊 Load Test Results:")
    print("=" * 60)
    
    successes = sum(1 for r in results if r["success"])
    failures = concurrency - successes
    latencies = [r["latency_sec"] for r in results]
    avg_latency = sum(latencies) / len(latencies) if latencies else 0
    max_latency = max(latencies) if latencies else 0
    min_latency = min(latencies) if latencies else 0
    
    # Sort for P95 latency
    latencies.sort()
    p95_index = int(len(latencies) * 0.95)
    p95_latency = latencies[p95_index] if latencies else 0

    print(f"Total Requests: {concurrency}")
    print(f"Success Rate:   {successes}/{concurrency} ({successes / concurrency * 100:.1f}%)")
    print(f"Failures:       {failures}")
    print(f"Total Time:     {total_test_time:.3f} seconds")
    print(f"Min Latency:    {min_latency:.3f} seconds")
    print(f"Max Latency:    {max_latency:.3f} seconds")
    print(f"Avg Latency:    {avg_latency:.3f} seconds")
    print(f"P95 Latency:    {p95_latency:.3f} seconds")
    print("=" * 60)
    
    for r in results:
        status = "✅ OK" if r["success"] else "❌ FAIL"
        print(f"Req #{r['id']}: Status={r['status_code']} | Latency={r['latency_sec']:.3f}s | {status} | Response: {r['response_text']}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PR Review Agent Load Testing CLI")
    parser.add_argument("--url", default="http://localhost:8000/webhook", help="Webhook URL to target")
    parser.add_argument("--concurrency", type=int, default=5, help="Number of concurrent requests to dispatch")
    parser.add_argument("--secret", default="testsecret", help="HMAC validation secret key")
    
    args = parser.parse_args()
    asyncio.run(run_load_test(args.url, args.concurrency, args.secret))
