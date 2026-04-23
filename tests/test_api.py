import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

from fastapi.testclient import TestClient
from main import app


client = TestClient(app)


def test_health_returns_200():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"message": "healthy"}


def test_create_job_returns_job_id():
    response = client.post("/jobs")
    assert response.status_code == 200
    data = response.json()
    assert "job_id" in data
    assert len(data["job_id"]) > 0


def test_get_job_returns_status():
    create_resp = client.post("/jobs")
    job_id = create_resp.json()["job_id"]
    response = client.get(f"/jobs/{job_id}")
    assert response.status_code == 200
    assert response.json()["status"] == "queued"


def test_get_nonexistent_job_returns_404():
    response = client.get("/jobs/nonexistent-id")
    assert response.status_code == 404


def test_create_multiple_jobs():
    ids = set()
    for _ in range(3):
        resp = client.post("/jobs")
        ids.add(resp.json()["job_id"])
    assert len(ids) == 3
