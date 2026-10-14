"""Real-network validation of the proposed fresh_connection ACTS grammar.

Network tests require a2a-sdk[http-server] and uvicorn; the fixture stays outside
scenarios/acts because that directory mirrors the upstream corpus verbatim.
"""

from __future__ import annotations
import asyncio
import json
import socket
import time
from pathlib import Path
import httpx
import pytest
from pydantic import ValidationError
from test_suite.acts import load_suite
from test_suite.acts.dispatcher import JsonRpcDispatcher, RestDispatcher
from test_suite.acts.runner import Outcome, Runner
from test_suite.acts.schema import Test as ActsTest, RunnerRequirement

FIXTURE = Path(__file__).parent / "fixtures/h09/auth-security.acts.yaml"


def candidate():
    return load_suite(FIXTURE).by_id("SEC-AUTH-007").test


@pytest.mark.parametrize(
    "change", ["requirement", "transport", "grpc", "stream", "repeat", "raw"]
)
def test_invalid_contract(change):
    data = candidate().model_dump(exclude_none=True, mode="json")
    if change == "requirement":
        data["runner_requirements"] = []
    elif change == "transport":
        data.pop("transport")
    elif change == "grpc":
        data["transport"] = ["grpc"]
    elif change == "stream":
        data["steps"][1]["operation"] = "subscribe_to_task"
    elif change == "repeat":
        data["steps"][1]["repeat"] = {"until": "status.state == X", "max_attempts": 2}
    else:
        data["steps"][1].pop("operation")
        data["steps"][1].pop("params")
        data["steps"][1]["raw"] = {"method": "GET", "path": "/tasks/x"}
    with pytest.raises(ValidationError):
        ActsTest.model_validate(data)


@pytest.mark.parametrize("advertise", [False, True])
def test_injected_client_skips(advertise):
    async def go():
        calls = []
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: calls.append(request) or httpx.Response(500)
            )
        ) as client:
            dispatcher = JsonRpcDispatcher("http://sut.test", client=client)
            result = await Runner(
                dispatcher,
                capabilities=[RunnerRequirement.FRESH_CONNECTION] if advertise else [],
            ).run_test(candidate())
        assert result.result is Outcome.SKIP
        assert not result.steps and not calls

    asyncio.run(go())


@pytest.fixture
def sdk_server(tmp_path, request):
    # The ITK and SDK generate different a2a.proto descriptors. A real process
    # boundary prevents descriptor-pool collisions, as in deployed services.
    import subprocess
    import sys

    pytest.importorskip("uvicorn")
    pytest.importorskip("a2a")
    mutation = request.node.callspec.params["mutation"]
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with (tmp_path / "server.log").open("w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                str(FIXTURE.with_name("sdk_server.py")),
                str(port),
                mutation or "none",
                str(tmp_path),
            ],
            stdout=log,
            stderr=log,
        )
        deadline = time.monotonic() + 8
        try:
            while (
                not (tmp_path / "ready").exists()
                and process.poll() is None
                and time.monotonic() < deadline
            ):
                time.sleep(0.02)
            assert (tmp_path / "ready").exists(), (tmp_path / "server.log").read_text()
            yield port, tmp_path
        finally:
            process.terminate()
            process.wait(timeout=5)


@pytest.mark.parametrize("binding", ["jsonrpc", "rest"])
@pytest.mark.parametrize(
    "mutation", [None, "state", "id", "context", "message", "missing", "reuse"]
)
def test_real_sdk_and_failure_controls(sdk_server, binding, mutation):
    port, directory = sdk_server
    cls = JsonRpcDispatcher if binding == "jsonrpc" else RestDispatcher

    async def go():
        async with cls(
            f"http://127.0.0.1:{port}/{binding}/",
            default_headers={"Authorization": "Bearer acts-fixture"},
        ) as dispatcher:
            if mutation == "reuse":
                dispatcher.dispatch_fresh = dispatcher.dispatch
            return await Runner(
                dispatcher,
                capabilities=[RunnerRequirement.FRESH_CONNECTION],
                sut_behaviors=["tck-auth-required"],
            ).run_test(candidate())

    result = asyncio.run(go())
    assert result.result is (
        Outcome.PASS if mutation in (None, "reuse") else Outcome.FAIL
    ), result
    records = [
        json.loads(line)
        for line in (directory / "requests.jsonl").read_text().splitlines()
    ]
    assert len(records) == 2
    if mutation == "reuse":
        # The protocol assertions alone pass, but the independent socket
        # evidence must catch a runner silently downgrading the requirement.
        assert records[0]["client"] == records[1]["client"]
        with pytest.raises(AssertionError, match="reused original TCP connection"):
            assert records[0]["client"] != records[1]["client"], (
                "reused original TCP connection"
            )
    else:
        assert records[0]["client"] != records[1]["client"], (
            "reused original TCP connection"
        )
    assert records[1]["get"] is True
    assert all(
        r["authorization"] == "Bearer acts-fixture" and r["version"] == "1.0"
        for r in records
    )
    assert "h09=session" in records[1]["cookie"]
    if mutation not in (None, "reuse"):
        assert (
            result.steps[0].result is Outcome.PASS
            and result.steps[1].result is Outcome.FAIL
        )
    print(
        json.dumps(
            {
                "binding": binding,
                "mutation": mutation,
                "outcome": result.result.value,
                "freshness_control_rejected": mutation == "reuse",
                "requests": records,
                "checks": sum(s.checks for s in result.steps),
            }
        )
    )
