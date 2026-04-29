"""Tests for backward-compatible job endpoints and health check."""


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"message": "healthy"}


def test_create_job(client):
    response = client.post("/jobs")
    assert response.status_code == 200
    data = response.json()
    assert "job_id" in data


def test_get_job(client):
    create = client.post("/jobs")
    job_id = create.json()["job_id"]
    response = client.get(f"/jobs/{job_id}")
    assert response.status_code == 200
    assert response.json()["status"] == "queued"


def test_get_job_not_found(client):
    response = client.get("/jobs/nonexistent-id")
    assert response.status_code == 404


def test_create_multiple_jobs(client):
    ids = set()
    for _ in range(5):
        resp = client.post("/jobs")
        ids.add(resp.json()["job_id"])
    assert len(ids) == 5
