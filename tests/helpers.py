PASSWORD = "correct horse battery"

GOOD_STATUS = {
    "screenDetected": True,
    "visibilityPercentage": 100,
    "clarity": "good",
    "lighting": "good",
    "stability": "stable",
    "alignment": "correct",
    "readinessScore": 96,
}


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def new_user(client, email: str, admin_code: str | None = None) -> str:
    body = {"name": email, "email": email, "password": PASSWORD}
    if admin_code:
        body["adminCode"] = admin_code
    response = await client.post("/api/auth/register", json=body)
    assert response.status_code == 201, response.text
    return response.json()["token"]


async def new_interview(client, token: str, candidate_name: str = "Sam Candidate") -> dict:
    response = await client.post(
        "/api/interviews",
        headers=bearer(token),
        json={"title": "Backend engineer", "candidateName": candidate_name, "scheduledAt": "2030-01-02T10:00:00.000Z"},
    )
    assert response.status_code == 201, response.text
    return response.json()["interview"]


async def join_as(client, interview_id: str, candidate_name: str = "Sam Candidate") -> str:
    response = await client.post(
        f"/api/interviews/{interview_id}/join", json={"candidateName": candidate_name, "consentAccepted": True}
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


async def event_types(client, token: str, interview_id: str) -> list[str]:
    response = await client.get(f"/api/interviews/{interview_id}/events", headers=bearer(token))
    assert response.status_code == 200, response.text
    return [event["eventType"] for event in response.json()["events"]]
