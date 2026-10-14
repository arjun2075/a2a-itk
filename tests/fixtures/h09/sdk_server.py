"""Isolated SDK fixture: avoids the runner's protobuf descriptor namespace."""

import json
import sys
from pathlib import Path
import uvicorn

port, mutation, directory = int(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
mutation = None if mutation == "none" else mutation
record_path = directory / "requests.jsonl"
from starlette.applications import Starlette
from starlette.routing import Mount
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_jsonrpc_routes, create_rest_routes
from a2a.server.tasks import TaskUpdater, InMemoryTaskStore
from a2a.types.a2a_pb2 import (
    AgentCard,
    AgentCapabilities,
    Task,
    TaskStatus,
    TaskState,
    Part,
)


class Executor(AgentExecutor):
    async def execute(self, context, queue):
        await queue.enqueue_event(
            Task(
                id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                history=[context.message],
            )
        )
        updater = TaskUpdater(queue, context.task_id, context.context_id)
        await updater.update_status(TaskState.TASK_STATE_WORKING)
        await updater.requires_auth(
            updater.new_agent_message([Part(text="authorization required")])
        )

    async def cancel(self, context, queue):
        raise NotImplementedError


card = AgentCard(
    name="H09 fixture",
    description="AUTH_REQUIRED fixture",
    version="1",
    capabilities=AgentCapabilities(),
    default_input_modes=["text/plain"],
    default_output_modes=["text/plain"],
)
handler = DefaultRequestHandler(
    agent_executor=Executor(), agent_card=card, task_store=InMemoryTaskStore()
)
app = Starlette(
    routes=[
        Mount(
            "/jsonrpc",
            Starlette(
                routes=create_jsonrpc_routes(request_handler=handler, rpc_url="/")
            ),
        ),
        Mount("/rest", Starlette(routes=create_rest_routes(request_handler=handler))),
    ]
)
records, control = [], {"mutation": mutation}


async def instrument(scope, receive, send):
    if scope["type"] != "http":
        return await app(scope, receive, send)
    headers, chunks, more = dict(scope["headers"]), [], True
    while more:
        message = await receive()
        chunks.append(message.get("body", b""))
        more = message.get("more_body", False)
    body = b"".join(chunks)
    envelope = json.loads(body) if body else {}
    is_get = envelope.get("method") == "GetTask" or scope["method"] == "GET"
    record = {
        "client": list(scope["client"]),
        "path": scope["path"],
        "get": is_get,
        "authorization": headers.get(b"authorization", b"").decode(),
        "cookie": headers.get(b"cookie", b"").decode(),
        "version": headers.get(b"a2a-version", b"").decode(),
    }
    records.append(record)
    with record_path.open("a") as output:
        output.write(json.dumps(record) + "\n")
    if record["authorization"] != "Bearer acts-fixture" or (
        is_get and "h09=session" not in record["cookie"]
    ):
        await send({"type": "http.response.start", "status": 401, "headers": []})
        return await send({"type": "http.response.body", "body": b"{}"})
    consumed = False

    async def replay():
        nonlocal consumed
        if not consumed:
            consumed = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    messages = []

    async def collect(message):
        messages.append(message)

    await app(scope, replay, collect)
    start = messages[0]
    content = b"".join(m.get("body", b"") for m in messages[1:])
    if not is_get:
        start["headers"].append((b"set-cookie", b"h09=session; Path=/; HttpOnly"))
    elif control["mutation"]:
        data = json.loads(content)
        task = data.get("result", data)
        mutation = control["mutation"]
        if mutation == "state":
            task["status"]["state"] = "TASK_STATE_SUBMITTED"
        elif mutation == "id":
            task["id"] = "wrong-task"
        elif mutation == "context":
            task["contextId"] = "wrong-context"
        elif mutation == "message":
            task["status"].pop("message", None)
        elif mutation == "missing":
            data = (
                {
                    "jsonrpc": "2.0",
                    "id": envelope.get("id"),
                    "error": {"code": -32001, "message": "task not found"},
                }
                if envelope
                else {"code": 5, "message": "task not found"}
            )
            if not envelope:
                start["status"] = 404
        content = json.dumps(data).encode()
        start["headers"] = [
            (k, v) for k, v in start["headers"] if k != b"content-length"
        ]
    await send(start)
    await send({"type": "http.response.body", "body": content})


class Server(uvicorn.Server):
    async def startup(self, sockets=None):
        await super().startup(sockets=sockets)
        if self.started:
            (directory / "ready").touch()


Server(
    uvicorn.Config(
        instrument, host="127.0.0.1", port=port, log_level="error", lifespan="off"
    )
).run()
