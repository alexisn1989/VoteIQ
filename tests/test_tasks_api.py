from __future__ import annotations

import os
import tempfile
import unittest

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from voteiq.api.routes.tasks import router
except ModuleNotFoundError:
    FastAPI = None
    TestClient = None
    router = None


@unittest.skipIf(FastAPI is None, "FastAPI is not installed in this environment")
class TasksApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        os.environ["TASKS_DB_PATH"] = os.path.join(self._tmp.name, "tasks.db")
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        os.environ.pop("TASKS_DB_PATH", None)
        self._tmp.cleanup()

    def test_create_list_update_and_delete_task(self) -> None:
        created = self.client.post(
            "/api/tasks",
            json={"title": "Write API docs", "description": "Document the tasks API", "priority": "high"},
        )
        self.assertEqual(created.status_code, 201)
        task = created.json()
        self.assertEqual(task["title"], "Write API docs")
        self.assertEqual(task["status"], "todo")
        self.assertEqual(task["priority"], "high")

        task_id = task["id"]
        fetched = self.client.get(f"/api/tasks/{task_id}")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json()["id"], task_id)

        updated = self.client.patch(
            f"/api/tasks/{task_id}",
            json={"status": "done", "description": "Published"},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["status"], "done")
        self.assertEqual(updated.json()["description"], "Published")

        listed = self.client.get("/api/tasks", params={"status": "done"})
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["count"], 1)

        deleted = self.client.delete(f"/api/tasks/{task_id}")
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(self.client.get(f"/api/tasks/{task_id}").status_code, 404)

    def test_rejects_invalid_status_and_priority(self) -> None:
        bad_status = self.client.post("/api/tasks", json={"title": "Bad", "status": "blocked"})
        self.assertEqual(bad_status.status_code, 422)

        bad_priority = self.client.post("/api/tasks", json={"title": "Bad", "priority": "urgent"})
        self.assertEqual(bad_priority.status_code, 422)

    def test_search_filters_title_and_description(self) -> None:
        self.client.post("/api/tasks", json={"title": "Alpha task", "description": "Plain"})
        self.client.post("/api/tasks", json={"title": "Beta task", "description": "Needs docs"})

        result = self.client.get("/api/tasks", params={"q": "docs"})
        self.assertEqual(result.status_code, 200)
        body = result.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["tasks"][0]["title"], "Beta task")


if __name__ == "__main__":
    unittest.main()
