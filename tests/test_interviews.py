import jwt

from .helpers import GOOD_STATUS, bearer, event_types, join_as, new_interview, new_user


async def livekit_token(client, token, interview_id):
    return await client.post("/api/livekit/token", headers=bearer(token), json={"interviewId": interview_id})


async def post_status(client, token, interview_id, source="stream_analysis", **overrides):
    return await client.post(
        "/api/monitoring/status",
        headers=bearer(token),
        json={"interviewId": interview_id, "source": source, **GOOD_STATUS, **overrides},
    )


class TestInterviewManagement:
    async def test_requires_authentication(self, client):
        assert (await client.get("/api/interviews")).status_code == 401
        assert (await client.post("/api/interviews", json={"title": "x", "candidateName": "y"})).status_code == 401

    async def test_generates_an_unguessable_typeable_id_and_a_matching_room(self, client):
        interview = await new_interview(client, await new_user(client, "a@example.com"))
        assert len(interview["id"]) == 14 and interview["id"].count("-") == 2
        assert not set(interview["id"].replace("-", "")) & set("ILOU")
        assert interview["livekitRoomName"] == f"interview-{interview['id']}"
        assert interview["status"] == "SCHEDULED"
        assert interview["scheduledAt"] == "2030-01-02T10:00:00.000Z"
        assert interview["monitoringStatus"] is None

    async def test_validates_input(self, client):
        token = await new_user(client, "a@example.com")
        post = lambda body: client.post("/api/interviews", headers=bearer(token), json=body)  # noqa: E731
        assert (await post({"title": "", "candidateName": "x"})).status_code == 400
        assert (await post({"title": "x", "candidateName": "y", "scheduledAt": "tomorrow"})).status_code == 400
        assert (await post({"title": "x", "candidateName": "y", "scheduledAt": "2030-01-02T10:00:00"})).status_code == 400
        assert (await post({"title": "x", "candidateName": "y", "scheduledAt": None})).status_code == 201

    async def test_keeps_interviewers_out_of_each_others_interviews_through_every_action(self, client, lk):
        owner = await new_user(client, "owner@example.com")
        other = await new_user(client, "other@example.com")
        interview_id = (await new_interview(client, owner))["id"]

        assert (await client.get(f"/api/interviews/{interview_id}", headers=bearer(owner))).status_code == 200
        assert (await client.get(f"/api/interviews/{interview_id}", headers=bearer(other))).status_code == 403
        assert (await client.get(f"/api/interviews/{interview_id}/events", headers=bearer(other))).status_code == 403
        assert (await client.post(f"/api/interviews/{interview_id}/end", headers=bearer(other), json={})).status_code == 403
        assert (await livekit_token(client, other, interview_id)).status_code == 403
        assert (await post_status(client, other, interview_id)).status_code == 403
        assert (await client.get("/api/interviews", headers=bearer(other))).json()["interviews"] == []
        assert len((await client.get("/api/interviews", headers=bearer(owner))).json()["interviews"]) == 1

    async def test_lets_admins_see_and_manage_every_interview(self, client):
        owner = await new_user(client, "owner@example.com")
        admin = await new_user(client, "admin@example.com", "test-admin-code")
        interview_id = (await new_interview(client, owner))["id"]
        assert len((await client.get("/api/interviews", headers=bearer(admin))).json()["interviews"]) == 1
        assert (await client.get(f"/api/interviews/{interview_id}", headers=bearer(admin))).status_code == 200

    async def test_returns_404_for_an_unknown_interview(self, client):
        token = await new_user(client, "a@example.com")
        assert (await client.get("/api/interviews/AAAA-BBBB-CCCC", headers=bearer(token))).status_code == 404

    async def test_lists_newest_first(self, client):
        token = await new_user(client, "a@example.com")
        first = await new_interview(client, token)
        second = await new_interview(client, token)
        ids = [item["id"] for item in (await client.get("/api/interviews", headers=bearer(token))).json()["interviews"]]
        assert ids == [second["id"], first["id"]]


class TestCandidateAccess:
    async def test_requires_the_right_name_and_answers_wrong_name_and_wrong_id_identically(self, client):
        interview_id = (await new_interview(client, await new_user(client, "a@example.com")))["id"]
        wrong_name = await client.post(
            f"/api/interviews/{interview_id}/join", json={"candidateName": "Someone Else", "consentAccepted": True}
        )
        wrong_id = await client.post(
            "/api/interviews/ZZZZ-ZZZZ-ZZZZ/join", json={"candidateName": "Sam Candidate", "consentAccepted": True}
        )
        assert wrong_name.status_code == wrong_id.status_code == 404
        assert wrong_name.json() == wrong_id.json()

    async def test_requires_consent_to_monitoring(self, client):
        interview_id = (await new_interview(client, await new_user(client, "a@example.com")))["id"]
        for consent in (False, "yes", None):
            response = await client.post(
                f"/api/interviews/{interview_id}/join", json={"candidateName": "Sam Candidate", "consentAccepted": consent}
            )
            assert response.status_code == 400

    async def test_tolerates_differences_in_how_the_name_and_id_are_typed(self, client):
        interview_id = (await new_interview(client, await new_user(client, "a@example.com")))["id"]
        response = await client.post(
            f"/api/interviews/{interview_id.lower().replace('-', '%20')}/join",
            json={"candidateName": "  sam   CANDIDATE ", "consentAccepted": True},
        )
        assert response.status_code == 200

    async def test_returns_the_readiness_threshold_and_limited_details(self, client):
        interview_id = (await new_interview(client, await new_user(client, "a@example.com")))["id"]
        body = (
            await client.post(
                f"/api/interviews/{interview_id}/join", json={"candidateName": "Sam Candidate", "consentAccepted": True}
            )
        ).json()
        assert body["readinessThreshold"] == 80
        assert "interviewerId" not in body["interview"]
        assert "livekitRoomName" not in body["interview"]
        assert set(body["interview"]) == {"id", "title", "candidateName", "status", "startedAt", "endedAt"}

    async def test_confines_a_candidate_token_to_its_own_interview(self, client, lk):
        owner = await new_user(client, "a@example.com")
        mine = await new_interview(client, owner)
        theirs = await new_interview(client, owner)
        token = await join_as(client, mine["id"])

        assert (await client.get(f"/api/interviews/{mine['id']}", headers=bearer(token))).status_code == 200
        assert (await client.get(f"/api/interviews/{theirs['id']}", headers=bearer(token))).status_code == 403
        assert (await client.post(f"/api/interviews/{theirs['id']}/end", headers=bearer(token), json={})).status_code == 403
        assert (await livekit_token(client, token, theirs["id"])).status_code == 403
        assert (await client.get("/api/interviews", headers=bearer(token))).status_code == 401
        assert (await client.get(f"/api/interviews/{mine['id']}/events", headers=bearer(token))).status_code == 401

    async def test_refuses_joining_an_ended_interview(self, client, lk):
        owner = await new_user(client, "a@example.com")
        interview_id = (await new_interview(client, owner))["id"]
        await client.post(f"/api/interviews/{interview_id}/end", headers=bearer(owner), json={})
        response = await client.post(
            f"/api/interviews/{interview_id}/join", json={"candidateName": "Sam Candidate", "consentAccepted": True}
        )
        assert response.status_code == 410


class TestLiveKitTokens:
    async def test_candidate_token_is_short_lived_and_limited_to_camera_and_microphone(self, client, lk):
        interview = await new_interview(client, await new_user(client, "a@example.com"))
        token = await join_as(client, interview["id"])
        response = await livekit_token(client, token, interview["id"])
        body = response.json()
        assert response.status_code == 200
        lk.ensure_room.assert_awaited_with(interview["livekitRoomName"])
        assert body["roomName"] == interview["livekitRoomName"]
        assert body["identity"] == f"candidate-{interview['id']}"
        assert body["url"] == "ws://localhost:7880"

        claims = jwt.decode(body["token"], options={"verify_signature": False})
        video = claims["video"]
        assert video["room"] == interview["livekitRoomName"]
        assert video["roomJoin"] is True and video["canPublish"] is True
        assert video.get("canPublishData") is False
        assert [s.lower() for s in video["canPublishSources"]] == ["camera", "microphone"]
        assert not video.get("roomAdmin")
        assert claims["exp"] - claims["nbf"] <= 600

    async def test_interviewer_token_is_subscribe_only(self, client, lk):
        owner = await new_user(client, "a@example.com")
        interview_id = (await new_interview(client, owner))["id"]
        body = (await livekit_token(client, owner, interview_id)).json()
        video = jwt.decode(body["token"], options={"verify_signature": False})["video"]
        assert body["identity"].startswith("interviewer-")
        assert not video.get("canPublish") and not video.get("canPublishData")
        assert video["canSubscribe"] is True

    async def test_never_returns_the_api_secret(self, client, lk):
        owner = await new_user(client, "a@example.com")
        interview_id = (await new_interview(client, owner))["id"]
        response = await livekit_token(client, owner, interview_id)
        assert "devsecret-devsecret-devsecret-dev" not in response.text

    async def test_candidate_starts_the_interview_but_the_interviewer_does_not(self, client, lk):
        owner = await new_user(client, "a@example.com")
        interview_id = (await new_interview(client, owner))["id"]
        await livekit_token(client, owner, interview_id)
        first = (await client.get(f"/api/interviews/{interview_id}", headers=bearer(owner))).json()["interview"]
        assert first["status"] == "SCHEDULED"

        await livekit_token(client, await join_as(client, interview_id), interview_id)
        second = (await client.get(f"/api/interviews/{interview_id}", headers=bearer(owner))).json()["interview"]
        assert second["status"] == "ACTIVE" and second["startedAt"]

    async def test_reports_503_when_livekit_is_unreachable_without_starting_the_interview(self, client, lk):
        owner = await new_user(client, "a@example.com")
        interview_id = (await new_interview(client, owner))["id"]
        token = await join_as(client, interview_id)
        lk.ensure_room.side_effect = ConnectionError("connection refused")
        response = await livekit_token(client, token, interview_id)
        assert response.status_code == 503 and response.json()["error"]["code"] == "livekit_unavailable"
        lk.ensure_room.side_effect = None
        status = (await client.get(f"/api/interviews/{interview_id}", headers=bearer(owner))).json()["interview"]["status"]
        assert status == "SCHEDULED"


class TestMonitoring:
    async def active_interview(self, client):
        owner = await new_user(client, "a@example.com")
        interview = await new_interview(client, owner)
        candidate = await join_as(client, interview["id"])
        await livekit_token(client, candidate, interview["id"])
        return owner, candidate, interview["id"], interview["livekitRoomName"]

    async def test_turns_stream_analysis_changes_into_a_stored_timeline(self, client, lk):
        owner, _, interview_id, _ = await self.active_interview(client)
        assert (await post_status(client, owner, interview_id)).status_code == 200
        await post_status(client, owner, interview_id)
        await post_status(client, owner, interview_id, visibilityPercentage=50)
        await post_status(client, owner, interview_id, visibilityPercentage=90)
        await post_status(client, owner, interview_id, screenDetected=False, visibilityPercentage=0, alignment="unknown")

        assert await event_types(client, owner, interview_id) == [
            "consent_accepted",
            "candidate_joined",
            "screen_detected",
            "visibility_dropped",
            "positioning_corrected",
            "screen_lost",
        ]
        current = (await client.get(f"/api/interviews/{interview_id}", headers=bearer(owner))).json()["interview"][
            "monitoringStatus"
        ]
        assert current["screenDetected"] is False and current["source"] == "stream_analysis"

    async def test_pushes_status_and_warnings_to_the_room(self, client, lk):
        owner, _, interview_id, room = await self.active_interview(client)
        await post_status(client, owner, interview_id)
        sent = [call.args[1] for call in lk.broadcast.await_args_list if call.args[0] == room]
        types = [message["type"] for message in sent]
        assert "status" in types and "event" in types
        status = next(m for m in sent if m["type"] == "status")["status"]
        assert status["interviewId"] == interview_id and "readinessScore" in status  # camelCase on the wire

    async def test_holds_each_side_to_its_own_status_source(self, client, lk):
        owner, candidate, interview_id, _ = await self.active_interview(client)
        assert (await post_status(client, candidate, interview_id, "stream_analysis")).status_code == 403
        assert (await post_status(client, owner, interview_id, "calibration")).status_code == 403
        assert (await post_status(client, candidate, interview_id, "calibration")).status_code == 200

    async def test_does_not_derive_warnings_from_calibration(self, client, lk):
        owner, candidate, interview_id, _ = await self.active_interview(client)
        await post_status(client, candidate, interview_id, "calibration")
        await post_status(client, candidate, interview_id, "calibration", screenDetected=False, visibilityPercentage=0)
        assert "screen_lost" not in await event_types(client, owner, interview_id)

    async def test_rejects_out_of_range_and_malformed_status(self, client, lk):
        owner, _, interview_id, _ = await self.active_interview(client)
        assert (await post_status(client, owner, interview_id, visibilityPercentage=101)).status_code == 400
        assert (await post_status(client, owner, interview_id, readinessScore=-1)).status_code == 400
        assert (await post_status(client, owner, interview_id, clarity="amazing")).status_code == 400
        assert (await post_status(client, owner, interview_id, visibilityPercentage="50")).status_code == 400

    async def test_only_accepts_allowlisted_events_with_server_authored_wording(self, client, lk):
        owner, candidate, interview_id, _ = await self.active_interview(client)
        post = lambda token, body: client.post(  # noqa: E731
            "/api/monitoring/events", headers=bearer(token), json={"interviewId": interview_id, **body}
        )
        allowed = await post(candidate, {"eventType": "network_unstable", "message": "forged text", "severity": "INFO"})
        assert allowed.status_code == 201
        assert allowed.json()["event"]["message"] == "Network connection is unstable."
        assert allowed.json()["event"]["severity"] == "WARNING"

        assert (await post(candidate, {"eventType": "screen_detected"})).status_code == 403
        assert (await post(owner, {"eventType": "made_up"})).status_code == 403
        assert (await post(owner, {"eventType": "network_unstable", "metadata": {"k": "x" * 301}})).status_code == 400

    async def test_accepts_video_stop_reports_from_the_interviewer(self, client, lk):
        owner, _, interview_id, _ = await self.active_interview(client)
        response = await client.post(
            "/api/monitoring/events", headers=bearer(owner), json={"interviewId": interview_id, "eventType": "video_stopped"}
        )
        assert response.status_code == 201 and response.json()["event"]["severity"] == "CRITICAL"

    async def test_events_endpoint_supports_after_and_limit(self, client, lk):
        owner, _, interview_id, _ = await self.active_interview(client)
        events = (await client.get(f"/api/interviews/{interview_id}/events", headers=bearer(owner))).json()["events"]
        assert len(events) == 2
        limited = await client.get(f"/api/interviews/{interview_id}/events?limit=1", headers=bearer(owner))
        assert len(limited.json()["events"]) == 1
        future = await client.get(
            f"/api/interviews/{interview_id}/events?after=2999-01-01T00:00:00.000Z", headers=bearer(owner)
        )
        assert future.json()["events"] == []
        assert (await client.get(f"/api/interviews/{interview_id}/events?limit=0", headers=bearer(owner))).status_code == 400
        assert (await client.get(f"/api/interviews/{interview_id}/events?after=yesterday", headers=bearer(owner))).status_code == 400


class TestEnding:
    async def test_ends_for_everyone_status_room_realtime_message_and_further_candidate_access(self, client, lk):
        owner = await new_user(client, "a@example.com")
        interview = await new_interview(client, owner)
        interview_id, room = interview["id"], interview["livekitRoomName"]
        candidate = await join_as(client, interview_id)
        await livekit_token(client, candidate, interview_id)

        response = await client.post(f"/api/interviews/{interview_id}/end", headers=bearer(owner), json={})
        assert response.status_code == 200
        assert response.json()["interview"]["status"] == "COMPLETED" and response.json()["interview"]["endedAt"]
        lk.close_room.assert_awaited_with(room)
        lk.broadcast.assert_any_await(room, {"type": "session_ended", "interviewId": interview_id, "endedBy": "interviewer"})

        assert (await livekit_token(client, candidate, interview_id)).status_code == 410
        assert (await post_status(client, candidate, interview_id, "calibration")).status_code == 410
        gone = await client.post(
            "/api/monitoring/events", headers=bearer(candidate), json={"interviewId": interview_id, "eventType": "network_unstable"}
        )
        assert gone.status_code == 410
        # The candidate can still learn that it ended.
        seen = (await client.get(f"/api/interviews/{interview_id}", headers=bearer(candidate))).json()["interview"]
        assert seen["status"] == "COMPLETED"

    async def test_lets_the_candidate_end_it_and_is_safe_to_call_twice(self, client, lk):
        owner = await new_user(client, "a@example.com")
        interview_id = (await new_interview(client, owner))["id"]
        candidate = await join_as(client, interview_id)
        await livekit_token(client, candidate, interview_id)

        ended = await client.post(f"/api/interviews/{interview_id}/end", headers=bearer(candidate), json={})
        assert ended.json()["interview"]["status"] == "COMPLETED"
        assert "interviewerId" not in ended.json()["interview"]
        assert (await client.post(f"/api/interviews/{interview_id}/end", headers=bearer(owner), json={})).status_code == 200

        events = (await client.get(f"/api/interviews/{interview_id}/events", headers=bearer(owner))).json()["events"]
        session_ended = [event for event in events if event["eventType"] == "session_ended"]
        assert len(session_ended) == 1
        assert "candidate" in session_ended[0]["message"]
        assert session_ended[0]["metadata"] == {"endedBy": "candidate"}

    async def test_marks_an_interview_that_never_started_as_cancelled(self, client, lk):
        owner = await new_user(client, "a@example.com")
        interview_id = (await new_interview(client, owner))["id"]
        ended = await client.post(f"/api/interviews/{interview_id}/end", headers=bearer(owner), json={})
        assert ended.json()["interview"]["status"] == "CANCELLED"


class TestAnalytics:
    async def test_counts_only_the_callers_interviews(self, client, lk):
        owner = await new_user(client, "a@example.com")
        other = await new_user(client, "b@example.com")
        ended_id = (await new_interview(client, owner))["id"]
        await new_interview(client, owner)
        await new_interview(client, other)
        await client.post(f"/api/interviews/{ended_id}/end", headers=bearer(owner), json={})
        active_id = (await new_interview(client, owner))["id"]
        await livekit_token(client, await join_as(client, active_id), active_id)

        body = (await client.get("/api/analytics/overview", headers=bearer(owner))).json()
        assert body["totals"] == {"total": 3, "active": 1, "completed": 0, "upcoming": 1}
        assert len(body["interviewsByDay"]) == 14
        assert body["interviewsByDay"][-1]["count"] == 3
        assert [row["severity"] for row in body["eventsBySeverity"]] == ["INFO", "WARNING", "CRITICAL"]
        assert body["eventsBySeverity"][0]["count"] > 0
