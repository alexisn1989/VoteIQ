"""Task management REST API."""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

_BASE_DIR = Path(__file__).resolve().parents[3]

TaskStatus = Literal["todo", "in_progress", "done"]
TaskPriority = Literal["low", "medium", "high"]


class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    status: TaskStatus = "todo"
    priority: TaskPriority = "medium"
    due_date: str | None = Field(default=None, max_length=40)


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    due_date: str | None = Field(default=None, max_length=40)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _tasks_db_path() -> Path:
    explicit = os.getenv("TASKS_DB_PATH")
    if explicit:
        return Path(explicit)
    data_dir = Path(os.getenv("DATA_DIR", str(_BASE_DIR)))
    return data_dir / "tasks.db"


def _connect() -> sqlite3.Connection:
    db_path = _tasks_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_tasks_table() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'todo',
                priority TEXT NOT NULL DEFAULT 'medium',
                due_date TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority)")
        conn.commit()


def _row_to_task(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "status": row["status"],
        "priority": row["priority"],
        "due_date": row["due_date"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


@router.get("")
def list_tasks(
    task_status: TaskStatus | None = Query(default=None, alias="status"),
    priority: TaskPriority | None = None,
    q: str | None = Query(default=None, max_length=200),
) -> dict:
    """List tasks, optionally filtered by status, priority, or text search."""
    _ensure_tasks_table()
    where: list[str] = []
    params: list[str] = []

    if task_status:
        where.append("status = ?")
        params.append(task_status)
    if priority:
        where.append("priority = ?")
        params.append(priority)
    if q:
        where.append("(lower(title) LIKE ? OR lower(description) LIKE ?)")
        pattern = f"%{q.lower()}%"
        params.extend([pattern, pattern])

    sql = "SELECT * FROM tasks"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY CASE status WHEN 'todo' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END, id DESC"

    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return {"tasks": [_row_to_task(row) for row in rows], "count": len(rows)}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_task(payload: TaskCreate) -> dict:
    """Create a task."""
    _ensure_tasks_table()
    now = _utc_now()
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO tasks (title, description, status, priority, due_date, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.title.strip(),
                payload.description.strip(),
                payload.status,
                payload.priority,
                payload.due_date,
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _row_to_task(row)


@router.get("/{task_id}")
def get_task(task_id: int) -> dict:
    """Fetch one task by id."""
    _ensure_tasks_table()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Task not found")
    return _row_to_task(row)


@router.patch("/{task_id}")
def update_task(task_id: int, payload: TaskUpdate) -> dict:
    """Update one or more task fields."""
    _ensure_tasks_table()
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No task fields provided")

    if "title" in updates and updates["title"] is not None:
        updates["title"] = updates["title"].strip()
    if "description" in updates and updates["description"] is not None:
        updates["description"] = updates["description"].strip()

    assignments = [f"{field} = ?" for field in updates]
    params = list(updates.values())
    assignments.append("updated_at = ?")
    params.append(_utc_now())
    params.append(task_id)

    with _connect() as conn:
        existing = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Task not found")
        conn.execute(f"UPDATE tasks SET {', '.join(assignments)} WHERE id = ?", params)
        conn.commit()
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return _row_to_task(row)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(task_id: int) -> Response:
    """Delete a task by id."""
    _ensure_tasks_table()
    with _connect() as conn:
        cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Task not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
