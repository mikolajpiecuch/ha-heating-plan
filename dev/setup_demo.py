"""Add the home, two rooms and a demo override to a development Home Assistant.

Run it after scripts/develop has started: python dev/setup_demo.py
"""

from __future__ import annotations

import asyncio
import json

import aiohttp

URL = "http://localhost:8123"
CLIENT = f"{URL}/"
USER, PASSWORD = "dev", "dev"


async def token(session: aiohttp.ClientSession) -> str:
    async with session.post(
        f"{URL}/auth/login_flow",
        json={"client_id": CLIENT, "handler": ["homeassistant", None], "redirect_uri": CLIENT},
    ) as resp:
        flow = await resp.json()
    async with session.post(
        f"{URL}/auth/login_flow/{flow['flow_id']}",
        json={"client_id": CLIENT, "username": USER, "password": PASSWORD},
    ) as resp:
        result = await resp.json()
    async with session.post(
        f"{URL}/auth/token",
        data={"grant_type": "authorization_code", "code": result["result"], "client_id": CLIENT},
    ) as resp:
        return (await resp.json())["access_token"]


class Ws:
    def __init__(self, ws) -> None:
        self.ws = ws
        self.id = 0

    async def call(self, msg: dict) -> dict:
        self.id += 1
        await self.ws.send_json({"id": self.id, **msg})
        while True:
            reply = await self.ws.receive_json()
            if reply.get("id") == self.id and reply.get("type") == "result":
                if not reply["success"]:
                    raise RuntimeError(json.dumps(reply["error"]))
                return reply["result"]


async def main() -> None:
    async with aiohttp.ClientSession() as session:
        access = await token(session)
        headers = {"Authorization": f"Bearer {access}"}
        async with session.ws_connect(f"{URL}/api/websocket") as raw:
            await raw.receive_json()
            await raw.send_json({"type": "auth", "access_token": access})
            print(await raw.receive_json())
            ws = Ws(raw)
            areas = {a["area_id"]: a for a in await ws.call({"type": "config/area_registry/list"})}
            for name in ("Office", "Bedroom"):
                if name.lower() not in areas:
                    await ws.call({"type": "config/area_registry/create", "name": name})

        async def flow(path: str, data: dict | None = None) -> dict:
            async with session.post(f"{URL}{path}", json=data or {}, headers=headers) as resp:
                body = await resp.json()
                print(path, resp.status, body.get("type"), body.get("reason") or body.get("errors"))
                return body

        async with session.get(f"{URL}/api/config/config_entries/entry", headers=headers) as resp:
            entries = [e for e in await resp.json() if e["domain"] == "heating_plan"]
        if entries:
            entry_id = entries[0]["entry_id"]
        else:
            step = await flow("/api/config/config_entries/flow", {"handler": "heating_plan"})
            done = await flow(f"/api/config/config_entries/flow/{step['flow_id']}", {"name": "Dom"})
            entry_id = done["result"]["entry_id"]

        for name, area, climate, sensor in (
            ("Gabinet", "office", "climate.office_thermostat", None),
            ("Pokój gościnny", "bedroom", "climate.bedroom_thermostat", None),
        ):
            step = await flow(
                "/api/config/config_entries/subentries/flow",
                {"handler": [entry_id, "room"]},
            )
            data = {"name": name, "area_id": area, "climate_entity_id": climate, "override_hours": 3}
            if sensor:
                data["sensor_entity_id"] = sensor
            await flow(f"/api/config/config_entries/subentries/flow/{step['flow_id']}", data)

        await asyncio.sleep(5)
        async with session.ws_connect(f"{URL}/api/websocket") as raw:
            await raw.receive_json()
            await raw.send_json({"type": "auth", "access_token": access})
            await raw.receive_json()
            ws = Ws(raw)
            dashboards = await ws.call({"type": "lovelace/dashboards/list"})
            if not any(d["url_path"] == "heating-plan" for d in dashboards):
                await ws.call(
                    {
                        "type": "lovelace/dashboards/create",
                        "url_path": "heating-plan",
                        "title": "Ogrzewanie",
                        "icon": "mdi:radiator",
                        "mode": "storage",
                        "show_in_sidebar": True,
                        "require_admin": False,
                    }
                )
            await ws.call(
                {
                    "type": "lovelace/config/save",
                    "url_path": "heating-plan",
                    "config": {
                        "views": [
                            {
                                "title": "Ogrzewanie",
                                "path": "rooms",
                                "cards": [{"type": "custom:heating-plan-card"}],
                            }
                        ]
                    },
                }
            )
            rooms = await ws.call({"type": "heating_plan/rooms"})
            print(json.dumps([(r["name"], r["entity_id"], r["sync_state"]) for r in rooms]))
            await ws.call(
                {
                    "type": "call_service",
                    "domain": "heating_plan",
                    "service": "set_override",
                    "service_data": {"entity_id": rooms[0]["entity_id"], "temperature": 22.5},
                }
            )


if __name__ == "__main__":
    asyncio.run(main())
