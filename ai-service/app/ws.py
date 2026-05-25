"""
WebSocket hub for real-time collaborative features:
- Cursor position broadcasting
- Annotation sync (create/update/delete events)
- Presence (who is viewing this document)
"""
import json
import asyncio
from typing import Dict, Set
from fastapi import WebSocket, WebSocketDisconnect

class DocumentHub:
    def __init__(self):
        # doc_key → set of websockets
        self._rooms:     Dict[str, Set[WebSocket]] = {}
        # ws → (org_id, doc_name, user_email)
        self._meta:      Dict[WebSocket, dict]      = {}

    def _room_key(self, org_id: str, doc_name: str) -> str:
        return f"{org_id}:{doc_name}"

    async def connect(
        self,
        ws:         WebSocket,
        org_id:     str,
        doc_name:   str,
        user_email: str,
    ):
        await ws.accept()
        key = self._room_key(org_id, doc_name)
        self._rooms.setdefault(key, set()).add(ws)
        self._meta[ws] = {"org_id": org_id, "doc_name": doc_name, "email": user_email}

        # Broadcast join event
        await self._broadcast(key, ws, {
            "type":  "presence",
            "event": "joined",
            "email": user_email,
        })

    async def disconnect(self, ws: WebSocket):
        meta = self._meta.pop(ws, {})
        key  = self._room_key(meta.get("org_id", ""), meta.get("doc_name", ""))
        room = self._rooms.get(key, set())
        room.discard(ws)
        if not room:
            self._rooms.pop(key, None)
        else:
            await self._broadcast(key, ws, {
                "type":  "presence",
                "event": "left",
                "email": meta.get("email", ""),
            })

    async def broadcast_event(self, ws: WebSocket, message: dict):
        meta = self._meta.get(ws, {})
        key  = self._room_key(meta.get("org_id", ""), meta.get("doc_name", ""))
        await self._broadcast(key, ws, message)

    async def _broadcast(self, key: str, sender: WebSocket, message: dict):
        dead = set()
        for conn in self._rooms.get(key, set()):
            if conn is sender:
                continue
            try:
                await conn.send_text(json.dumps(message))
            except Exception:
                dead.add(conn)
        for d in dead:
            self._rooms.get(key, set()).discard(d)
            self._meta.pop(d, None)

    def get_room_members(self, org_id: str, doc_name: str) -> list[str]:
        key = self._room_key(org_id, doc_name)
        return [
            self._meta[ws]["email"]
            for ws in self._rooms.get(key, set())
            if ws in self._meta
        ]

hub = DocumentHub()