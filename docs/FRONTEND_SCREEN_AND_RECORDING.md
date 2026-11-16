# Frontend guide: Screen Analyzer and Recording APIs

New backend APIs for the interviewer dashboard:

1. **Screen Analyzer**: send a screenshot, get what application is on the candidate's screen.
2. **Recording**: upload the interview video in chunks to Azure Blob Storage.
3. **History and playback**: list past recordings and play them.

## Common rules

| Item | Value |
|---|---|
| Base URL | your API host, e.g. `https://api.example.com` |
| Who can call | **Interviewer (or admin) only.** A candidate token gets `403`. |
| Authorization | `Authorization: Bearer <token>` (token from `POST /api/auth/login`), **or** the session cookie set at login (send with `credentials: "include"`). |
| JSON requests | `Content-Type: application/json` |
| Field names | camelCase (`interviewId`), except the Screen Analyzer response, which uses snake_case (see below). |
| Interview ID | the same `id` returned by `POST /api/interviews`, e.g. `7K2M-9QXT-4HVB` |

**Error shape (all APIs):**
```json
{ "error": { "code": "chunks_missing", "message": "1 chunk(s) were never uploaded", "details": { "missing": [3] } } }
```
Show `error.message`; use `error.code` for logic.

---

# 1. Screen Analyzer

Send one screenshot of the candidate's screen. An AI vision model returns which application is open and whether the interviewer should review it.

| | |
|---|---|
| **URL** | `/api/monitoring/screen-analysis` |
| **Method** | `POST` |
| **Authorization** | Bearer token or session cookie (interviewer) |
| **Content-Type** | `application/json` |
| **Max body** | 8 MB |
| **Rate limit** | 30 requests per minute per interview |

### Payload
```json
{
  "interviewId": "7K2M-9QXT-4HVB",
  "image": "iVBORw0KGgoAAAANSUhEUg..."
}
```
- `image`: the screenshot as **base64** (PNG, JPEG, WEBP or GIF). A `data:image/png;base64,...` prefix is also accepted.
- Convert a canvas: `canvas.toDataURL("image/jpeg", 0.7)` and send the whole string.
- Send one image every few seconds, not every frame. JPEG at about 1280px wide is enough.

### Success response `200`
Exactly six fields (**snake_case**):
```json
{
  "application": "ChatGPT",
  "category": "AI_ASSISTANT",
  "activity": "ChatGPT webpage detected",
  "alert_level": "HIGH",
  "message": "ChatGPT detected on screen. Review recommended.",
  "confidence": 0.96
}
```

| Field | Values |
|---|---|
| `application` | `Windows Desktop`, `Visual Studio Code`, `PyCharm`, `IntelliJ IDEA`, `Chrome`, `Edge`, `Firefox`, `ChatGPT`, `Claude`, `Gemini`, `Microsoft Copilot`, `Terminal`, `Command Prompt`, `PowerShell`, `Microsoft Word`, `Excel`, `PowerPoint`, `Google Meet`, `Zoom`, `Other`, `UNKNOWN` (other app names are possible) |
| `category` | `DESKTOP` `CODE_EDITOR` `IDE` `WEB_BROWSER` `AI_ASSISTANT` `TERMINAL` `DOCUMENT_EDITOR` `SPREADSHEET` `PRESENTATION` `VIDEO_CALL` `FILE_MANAGER` `OTHER` `UNKNOWN` |
| `alert_level` | `HIGH` (review recommended), `MEDIUM` (application changed), `LOW` (normal) |
| `confidence` | number from 0 to 1 |

**How to show it**
- `HIGH` and `MEDIUM` are also saved to the interview timeline automatically (events `screen_app_review` and `screen_app_changed`), once per new application. The same app staying open does **not** repeat the alert, so you do not need to de-duplicate.
- `HIGH` means "please review", **not** "cheating proven". Do not label the candidate as cheating in the UI.
- `application: "UNKNOWN"` means the frame was unclear. Ignore it.

### Errors
| Status | `error.code` | Meaning |
|---|---|---|
| 400 | `invalid_image` | not valid base64, or not PNG/JPEG/WEBP/GIF |
| 401 | `unauthorized` | not logged in |
| 403 | `forbidden` | candidate token, or not your interview |
| 404 | `not_found` | unknown interview |
| 410 | `interview_ended` | interview already ended |
| 413 | `payload_too_large` | image over 8 MB |
| 429 | `rate_limited` | too many requests, slow down |
| 502 | `ai_unavailable` / `ai_invalid_response` | AI failed; just skip this frame and try the next |
| 503 | `ai_not_configured` | server setup problem |
| 504 | `ai_timeout` | AI too slow; skip this frame |

### Example
```js
const res = await fetch(`${API}/api/monitoring/screen-analysis`, {
  method: "POST",
  headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
  body: JSON.stringify({ interviewId, image: canvas.toDataURL("image/jpeg", 0.7) }),
});
if (res.ok) {
  const r = await res.json();            // r.application, r.alert_level, ...
}
```

---

# 2. Recording (upload the video)

Record the stream in the browser (e.g. `MediaRecorder`) and upload it in chunks. The backend stores it in Azure Blob Storage. Flow:

```
POST  /api/recordings                       -> get recording id
PUT   /api/recordings/{id}/chunks/0,1,2...  -> upload each chunk
POST  /api/recordings/{id}/complete         -> finish, video becomes playable
```

## 2.1 Start a recording

| | |
|---|---|
| **URL** | `/api/recordings` |
| **Method** | `POST` |
| **Authorization** | Bearer token or session cookie (interviewer) |
| **Content-Type** | `application/json` |

**Payload**
```json
{ "interviewId": "7K2M-9QXT-4HVB", "contentType": "video/webm" }
```
`contentType` is optional: `video/webm` (default) or `video/mp4`. It must match what `MediaRecorder` produces.

**Response `201`**
```json
{
  "recording": {
    "id": "c0mwerne9p001400010c2c1",
    "interviewId": "7K2M-9QXT-4HVB",
    "interviewTitle": "Backend engineer",
    "candidateName": "Sam Candidate",
    "status": "RECORDING",
    "contentType": "video/webm",
    "sizeBytes": null,
    "durationSeconds": null,
    "startedAt": "2026-11-13T09:21:40.000Z",
    "endedAt": null,
    "createdAt": "2026-11-13T09:21:40.000Z",
    "videoUrl": null
  }
}
```
Keep `recording.id` for the next calls.

Errors: `401`, `403`, `404`, `410 interview_ended`, `503 recording_storage_not_configured`.

## 2.2 Upload a chunk

| | |
|---|---|
| **URL** | `/api/recordings/{id}/chunks/{index}` |
| **Method** | `PUT` |
| **Authorization** | Bearer token or session cookie (interviewer) |
| **Content-Type** | any (e.g. `video/webm`) |

- `{index}` starts at **0** and goes up by 1 for every chunk (0, 1, 2, ...). Never skip a number.
- **Payload:** the raw bytes of the chunk as the request body. It is **not** JSON and **not** a form.
- Max **16 MB** per chunk. Use `mediaRecorder.start(5000)` for about 5 second chunks.
- Re-sending the same `{index}` replaces that chunk, so it is safe to retry after a network error.

**Response `204`** (empty body)

Errors: `400` (empty chunk or bad index), `403`, `404`, `409 recording_closed` (already completed), `413 payload_too_large`.

```js
let index = 0;
recorder.ondataavailable = async (e) => {
  if (!e.data.size) return;
  const i = index++;
  await fetch(`${API}/api/recordings/${recordingId}/chunks/${i}`, {
    method: "PUT",
    headers: { Authorization: `Bearer ${token}` },
    body: e.data,                        // the Blob, sent as-is
  });
};
recorder.start(5000);
```

## 2.3 Finish the recording

| | |
|---|---|
| **URL** | `/api/recordings/{id}/complete` |
| **Method** | `POST` |
| **Authorization** | Bearer token or session cookie (interviewer) |
| **Content-Type** | `application/json` |

**Payload**
```json
{ "chunkCount": 3, "durationSeconds": 12 }
```
- `chunkCount`: how many chunks you uploaded (indexes 0 to chunkCount-1). Wait for all chunk uploads to finish first.
- `durationSeconds`: optional.

**Response `200`**
```json
{
  "recording": {
    "id": "c0mwerne9p001400010c2c1",
    "interviewId": "7K2M-9QXT-4HVB",
    "interviewTitle": "Backend engineer",
    "candidateName": "Sam Candidate",
    "status": "READY",
    "contentType": "video/webm",
    "sizeBytes": 4500000,
    "durationSeconds": 12,
    "startedAt": "2026-11-13T09:21:40.000Z",
    "endedAt": "2026-11-13T09:21:52.000Z",
    "createdAt": "2026-11-13T09:21:40.000Z",
    "videoUrl": "/api/recordings/c0mwerne9p001400010c2c1/video"
  }
}
```
Calling it again is safe (it returns the same result).

Errors: `400 chunks_missing` (`error.details.missing` lists the missing indexes: upload them, then call again), `403`, `404`, `503`.

> Important: call **complete** when the interview or recording stops. A recording that is never completed is not playable.

---

# 3. History and playback

## 3.1 List recordings (interview history)

| | |
|---|---|
| **URL** | `/api/recordings` |
| **Method** | `GET` |
| **Authorization** | Bearer token or session cookie (interviewer) |
| **Payload** | none |

**Query parameters (optional)**
| Name | Meaning |
|---|---|
| `interviewId` | only recordings of this interview |
| `limit` | 1 to 200, default 50 |

Example: `GET /api/recordings?interviewId=7K2M-9QXT-4HVB`

**Response `200`** (newest first)
```json
{
  "recordings": [
    {
      "id": "c0mwerne9p001400010c2c1",
      "interviewId": "7K2M-9QXT-4HVB",
      "interviewTitle": "Backend engineer",
      "candidateName": "Sam Candidate",
      "status": "READY",
      "contentType": "video/webm",
      "sizeBytes": 4500000,
      "durationSeconds": 12,
      "startedAt": "2026-11-13T09:21:40.000Z",
      "endedAt": "2026-11-13T09:21:52.000Z",
      "createdAt": "2026-11-13T09:21:40.000Z",
      "videoUrl": "/api/recordings/c0mwerne9p001400010c2c1/video"
    }
  ]
}
```
- An interviewer sees only their own interviews. An admin sees all.
- `status`: `RECORDING` (still uploading), `READY` (playable), `FAILED`.
- `videoUrl` is `null` unless `status` is `READY`. It is a path: prepend your API base URL.
- The list does **not** contain the video, only the details and the URL.

For the full timeline of one interview (including screen alerts) use the existing `GET /api/interviews/{id}/events`.

## 3.2 Get one recording

| | |
|---|---|
| **URL** | `/api/recordings/{id}` |
| **Method** | `GET` |
| **Authorization** | Bearer token or session cookie (interviewer) |

**Response `200`**: `{ "recording": { ...same fields as above... } }`. Errors: `403`, `404`.

## 3.3 Play the video

| | |
|---|---|
| **URL** | `/api/recordings/{id}/video` (this is `videoUrl`) |
| **Method** | `GET` |
| **Authorization** | Bearer token or session cookie (interviewer) |
| **Payload** | none (the browser adds a `Range` header itself when seeking) |

**Response:** the **video bytes**, not JSON. `Content-Type` is `video/webm` or `video/mp4`. It returns `200` for the whole file, or `206 Partial Content` for a `Range` request. Seeking and progressive playback work.

**Option A: logged in with the session cookie (recommended)**
```html
<video controls src="https://api.example.com/api/recordings/RECORDING_ID/video"></video>
```
If the dashboard and the API are on different domains, the backend needs `COOKIE_SAMESITE=none` and `CORS_ORIGINS` set to the dashboard origin.

**Option B: logged in with a Bearer token** (a `<video>` tag cannot send headers, so download it as a Blob first)
```js
const res = await fetch(`${API}${rec.videoUrl}`, { headers: { Authorization: `Bearer ${token}` } });
const blob = await res.blob();
videoEl.src = URL.createObjectURL(blob);
// when finished: URL.revokeObjectURL(videoEl.src)
```
Option B downloads the whole file before playing, which is slow for long interviews.

Errors: `401`, `403` (not your interview), `404 not_found` (not READY yet), `416` (bad `Range`).

---

## Quick reference

| # | Purpose | Method | URL | Body |
|---|---|---|---|---|
| 1 | Analyse screenshot | POST | `/api/monitoring/screen-analysis` | JSON `{interviewId, image}` |
| 2a | Start recording | POST | `/api/recordings` | JSON `{interviewId, contentType?}` |
| 2b | Upload chunk | PUT | `/api/recordings/{id}/chunks/{index}` | raw video bytes |
| 2c | Finish recording | POST | `/api/recordings/{id}/complete` | JSON `{chunkCount, durationSeconds?}` |
| 3a | History list | GET | `/api/recordings?interviewId=` | none |
| 3b | One recording | GET | `/api/recordings/{id}` | none |
| 3c | Play video | GET | `/api/recordings/{id}/video` | none |

All of them need the interviewer's `Authorization: Bearer <token>` (or the session cookie).
