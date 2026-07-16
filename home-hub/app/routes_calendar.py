"""Family calendar + chores routes (Household Content, shared by the family).

Reads need any authenticated approved device; writes need the 'checklists'
privilege (household content shares one grant). Store logic + date math live
in calendar_store; this layer only shapes requests and maps store errors:
ValueError -> 400, missing row -> 404.
"""
from fastapi import APIRouter, Body, Depends

from . import auth, calendar_store
from .errors import HubError

router = APIRouter(prefix="/api/calendar", tags=["calendar"])

_write = auth.require_privilege("checklists")

_EVENT_FIELDS = ("title", "date", "time", "duration_min", "person", "notes", "recurrence")
_CHORE_FIELDS = ("title", "assignee", "cadence", "due_date", "rotation")


def _bad_request(e: ValueError) -> HubError:
    return HubError(400, str(e), "bad_request")


# ----------------------------------------------------------------------------
# Events
# ----------------------------------------------------------------------------
@router.get("/events")
def list_events(start: str | None = None, end: str | None = None,
                device=Depends(auth.require_authenticated)):
    if not start or not end:
        raise HubError(400, "start and end (YYYY-MM-DD) are required", "bad_request")
    try:
        return calendar_store.list_events(start, end)
    except ValueError as e:
        raise _bad_request(e)


@router.post("/events")
def create_event(payload: dict = Body(default={}), device=Depends(_write)):
    try:
        return calendar_store.add_event(
            payload.get("title"),
            payload.get("date"),
            time=payload.get("time"),
            duration_min=payload.get("duration_min"),
            person=payload.get("person"),
            notes=payload.get("notes"),
            recurrence=payload.get("recurrence"),
            created_by=device["username"],
        )
    except ValueError as e:
        raise _bad_request(e)


@router.put("/events/{event_id}")
def update_event(event_id: int, payload: dict = Body(default={}), device=Depends(_write)):
    fields = {k: payload[k] for k in _EVENT_FIELDS if k in payload}
    try:
        row = calendar_store.update_event(event_id, fields)
    except ValueError as e:
        raise _bad_request(e)
    if row is None:
        raise HubError(404, "Event not found", "not_found")
    return row


@router.delete("/events/{event_id}")
def delete_event(event_id: int, device=Depends(_write)):
    if not calendar_store.delete_event(event_id):
        raise HubError(404, "Event not found", "not_found")
    return {"ok": True}


# ----------------------------------------------------------------------------
# Chores
# ----------------------------------------------------------------------------
@router.get("/chores")
def list_chores(include_done: bool = False,
                device=Depends(auth.require_authenticated)):
    return calendar_store.list_chores(include_done=include_done)


@router.post("/chores")
def create_chore(payload: dict = Body(default={}), device=Depends(_write)):
    try:
        return calendar_store.add_chore(
            payload.get("title"),
            assignee=payload.get("assignee"),
            cadence=payload.get("cadence") or "once",
            due_date=payload.get("due_date"),
            rotation=payload.get("rotation"),
        )
    except ValueError as e:
        raise _bad_request(e)


@router.put("/chores/{chore_id}")
def update_chore(chore_id: int, payload: dict = Body(default={}), device=Depends(_write)):
    fields = {k: payload[k] for k in _CHORE_FIELDS if k in payload}
    try:
        row = calendar_store.update_chore(chore_id, fields)
    except ValueError as e:
        raise _bad_request(e)
    if row is None:
        raise HubError(404, "Chore not found", "not_found")
    return row


@router.post("/chores/{chore_id}/complete")
def complete_chore(chore_id: int, device=Depends(_write)):
    row = calendar_store.complete_chore(chore_id)
    if row is None:
        raise HubError(404, "Chore not found", "not_found")
    return row


@router.delete("/chores/{chore_id}")
def delete_chore(chore_id: int, device=Depends(_write)):
    if not calendar_store.delete_chore(chore_id):
        raise HubError(404, "Chore not found", "not_found")
    return {"ok": True}
