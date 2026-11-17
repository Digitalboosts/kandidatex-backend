# Frontend guide: Subscription, Analytics, Screen alerts, Summary and Recordings

Everything the frontend needs, one endpoint at a time: **URL, authorization, payload, response, errors**.

---

## 0. Common rules

| Item | Value |
|---|---|
| **Base URL** | `{BASE_URL}` — e.g. `http://localhost:8000` (local) or your deployed API host. All paths below start with `/api`. |
| **Format** | JSON bodies, `Content-Type: application/json`. JSON fields are **camelCase**. |
| **Timestamps** | ISO 8601 UTC with milliseconds, e.g. `2026-11-16T10:30:00.000Z`. |
| **Money** | `amount` is in the smallest unit (**paise**). `amountDisplay` is ready to show (e.g. `₹10`). |

### Authorization

Log in with `POST /api/auth/login` (or `register`). The response has a `token`, and the server also sets the httpOnly cookie `im_session`. Send **either**:

```
Authorization: Bearer <token>
```

or the cookie (`fetch(..., { credentials: "include" })`). Sessions last 8 hours. In this guide **"Auth: user"** means an interviewer or admin session. Interviewers only ever see their own data; admins see everyone's.

### Error shape (every error)

```json
{ "error": { "code": "interview_limit_reached", "message": "Human readable text", "details": { } } }
```

Common status codes: `400` invalid input · `401` not signed in · `402` subscription / limit · `403` no access · `404` not found · `409` conflict · `429` rate limited · `502/503/504` an upstream service (AI, Razorpay, storage) is failing.

---

## 1. Subscription

The product has **one plan, paid once** (no auto-renewal, no UPI Autopay). The user pays through Razorpay Checkout, and the backend verifies the payment. One payment gives a fixed number of interviews for `durationDays` days. When the days end, access stops and the user can pay again.

### 1.1 Get plan details

`GET {BASE_URL}/api/subscription/plan`

- **Auth:** none (public)
- **Payload:** none

**Response 200**

```json
{
  "plan": {
    "code": "monthly_100",
    "name": "Interview Detect Monthly",
    "description": "50 monitored interviews for 30 days. One-time payment, no auto-renewal.",
    "amount": 1000,
    "amountDisplay": "₹10",
    "currency": "INR",
    "durationDays": 30,
    "interviewsPerPeriod": 50
  }
}
```

### 1.2 Start a payment (checkout)

`POST {BASE_URL}/api/subscription/checkout`

- **Auth:** user
- **Payload:** none (empty body or `{}`)
- **Limit:** 10 per hour per user

**Response 201**

```json
{
  "keyId": "rzp_live_xxxxxxxx",
  "orderId": "order_Nxxxxxxxxxxxx",
  "plan": { "code": "monthly_100", "name": "Interview Detect Monthly", "amount": 1000, "amountDisplay": "₹10", "currency": "INR", "durationDays": 30, "interviewsPerPeriod": 50, "description": "..." }
}
```

Open Razorpay Checkout with `key: keyId` and `order_id: orderId` (this is a normal one-time payment, so QR / UPI / card all work):

```js
const rzp = new Razorpay({
  key: data.keyId,
  order_id: data.orderId,
  handler: (res) => verify(res),   // res = { razorpay_payment_id, razorpay_order_id, razorpay_signature }
});
rzp.open();
```

**Errors:** `409 already_subscribed` (user already has an active plan) · `429` · `502/503` (Razorpay problem).

### 1.3 Confirm the payment

`POST {BASE_URL}/api/subscription/verify`

- **Auth:** user
- **Payload** (the three values Razorpay Checkout returns, copied as-is):

```json
{
  "razorpayPaymentId": "pay_Nxxxxxxxxxxxx",
  "razorpayOrderId": "order_Nxxxxxxxxxxxx",
  "razorpaySignature": "9ef4dffbfd84f1318f6739a3ce19f9d85851857ae648f114332d8401e0949a3d"
}
```

**Response 200**

```json
{
  "status": "active",
  "subscription": {
    "status": "ACTIVE",
    "active": true,
    "plan": { "code": "monthly_100", "name": "Interview Detect Monthly", "amount": 1000, "amountDisplay": "₹10", "currency": "INR", "durationDays": 30, "interviewsPerPeriod": 50, "description": "..." },
    "currentPeriodStart": "2026-11-16T10:30:00.000Z",
    "currentPeriodEnd": "2026-12-16T10:30:00.000Z",
    "interviews": { "included": 100, "used": 0, "remaining": 100 }
  }
}
```

- `status: "active"` → show success.
- `status: "pending"` → the payment is not captured yet. Show "Payment processing" and poll `GET /api/subscription/me` every few seconds until `subscribed` is `true`.
- The backend sends the **welcome email** (first payment only) and the **subscription receipt email** automatically.

**Errors:** `400 invalid_signature` · `404 not_found` (order does not belong to this user) · `502/503`.

### 1.4 My subscription, interview limit and transactions

`GET {BASE_URL}/api/subscription/me`

- **Auth:** user
- **Payload:** none

Use this one call for the billing page and for the "interviews left" badge.

**Response 200**

```json
{
  "subscribed": true,
  "subscription": {
    "status": "ACTIVE",
    "active": true,
    "plan": { "code": "monthly_100", "name": "Interview Detect Monthly", "amount": 1000, "amountDisplay": "₹10", "currency": "INR", "durationDays": 30, "interviewsPerPeriod": 50, "description": "..." },
    "currentPeriodStart": "2026-11-16T10:30:00.000Z",
    "currentPeriodEnd": "2026-12-16T10:30:00.000Z",
    "interviews": { "included": 100, "used": 37, "remaining": 63 }
  },
  "interviews": { "included": 100, "used": 37, "remaining": 63 },
  "transactions": [
    {
      "id": "cm3abc123",
      "razorpayPaymentId": "pay_Nxxxxxxxxxxxx",
      "amount": 1000,
      "amountDisplay": "₹10",
      "currency": "INR",
      "status": "CAPTURED",
      "method": "upi",
      "createdAt": "2026-11-16T10:30:00.000Z"
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `subscribed` | `true` when the user may create interviews. Use it to gate the UI. |
| `subscription` | `null` if the user never started checkout. |
| `subscription.status` | `CREATED` (checkout started, not paid) or `ACTIVE`. After `currentPeriodEnd` the plan is over and `subscribed` is `false`. |
| `interviews.included / used / remaining` | The limit for the paid period. It is `0 / 0 / 0` when not subscribed. |
| `transactions` | Payment history, newest first, at most 100. `status` is `CAPTURED` or `FAILED`. |

The plan works until `currentPeriodEnd` (payment time + `durationDays`).

### 1.5 Razorpay webhook (backend only)

`POST {BASE_URL}/api/subscription/webhook` — called by Razorpay, **not by the frontend**. On `payment.captured` it records the payment, starts the paid period with `used` = 0, and sends the receipt email.

### 1.6 What happens when the limit is reached

`POST /api/interviews` (create an interview) checks the plan first. Handle these errors:

| Status | `error.code` | When | Suggested UI |
|---|---|---|---|
| `402` | `subscription_required` | No active plan (never paid, or the period ended) | Show the plan and a "Pay" button |
| `402` | `interview_limit_reached` | All interviews of the plan are used. `details` = `{ "included": 50, "used": 50 }` | Show "Limit reached", with a "Buy again" button once the plan ends |

Admins are not limited.

---

## 2. Analytics (dashboard numbers)

`GET {BASE_URL}/api/analytics/overview`

- **Auth:** user
- **Payload:** none

**Response 200**

```json
{
  "totals": { "total": 37, "active": 1, "completed": 30, "upcoming": 6 },
  "interviewsByDay": [
    { "date": "2026-11-03", "count": 2 },
    { "date": "2026-11-04", "count": 0 }
  ],
  "eventsBySeverity": [
    { "severity": "INFO", "count": 120 },
    { "severity": "WARNING", "count": 14 },
    { "severity": "CRITICAL", "count": 3 }
  ]
}
```

- `totals`: interview counts by status (`upcoming` = scheduled).
- `interviewsByDay`: the last **14 days**, oldest first, UTC. Days with no interviews appear with `count: 0`.
- `eventsBySeverity`: timeline events from the same 14 days.

Combine this with `GET /api/subscription/me` (section 1.4) to show **"37 / 100 interviews used"** beside the charts.

---

## 3. Screen analysis (screenshot → AI result)

`POST {BASE_URL}/api/monitoring/screen-analysis`

- **Auth:** user (the interviewer's dashboard only; candidates get `403`)
- **Limits:** body up to **8 MB**, **30 requests per minute** per interview. Image must be PNG, JPEG, WEBP or GIF.
- **Payload:**

```json
{
  "interviewId": "7K2M-9QXT-4HVB",
  "image": "data:image/png;base64,iVBORw0KGgo..."
}
```

`image` is base64. The `data:image/...;base64,` prefix is optional.

**Response 200** (note: these six fields are **snake_case**, not camelCase)

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

| `alert_level` | Meaning |
|---|---|
| `HIGH` | An AI assistant or a configured high-priority app is visible. **Stored automatically** (see section 4). |
| `MEDIUM` | The visible application changed. A timeline event is added, but nothing is stored. |
| `LOW` | Normal screen. Nothing is added. |

A HIGH alert means *review recommended*, not proof of misuse. The same application staying on screen does **not** raise a second alert.

**What the backend does on a new HIGH alert**

1. Uploads the screenshot to the private Azure container `interviews-images`.
2. Saves a `ScreenAlert` row: the six fields, the interviewer's `userId`, the `interviewId`, and the blob path and blob URL.
3. Adds a timeline event `screen_app_review` whose `metadata.screenAlertId` points to that row.

**Errors:** `400 invalid_image` · `403` · `404` · `410` (interview ended) · `429` · `502 ai_unavailable / ai_invalid_response` · `503 ai_not_configured` · `504 ai_timeout`. On an AI error nothing is stored and no fake analysis is returned.

---

## 4. Stored screen alerts

Auth for all four: **user** (owner of the interview, or admin).

### 4.1 Alerts of one interview

`GET {BASE_URL}/api/interviews/{interviewId}/screen-alerts`

**Response 200**

```json
{
  "screenAlerts": [
    {
      "id": "cm3alert001",
      "interviewId": "7K2M-9QXT-4HVB",
      "userId": "cm3user001",
      "application": "ChatGPT",
      "category": "AI_ASSISTANT",
      "activity": "ChatGPT webpage detected",
      "alertLevel": "HIGH",
      "message": "ChatGPT detected on screen. Review recommended.",
      "confidence": 0.96,
      "imageUrl": "/api/screen-alerts/cm3alert001/image",
      "blobUrl": "https://<account>.blob.core.windows.net/interviews-images/cm3user001/7K2M-9QXT-4HVB/cm3alert001.png",
      "createdAt": "2026-11-16T10:31:12.000Z"
    }
  ],
  "summary": {
    "interviewId": "7K2M-9QXT-4HVB",
    "userId": "cm3user001",
    "summary": "During this interview, the monitoring system raised ...",
    "wordCount": 110,
    "alertCount": 1,
    "createdAt": "2026-11-16T10:45:00.000Z",
    "updatedAt": "2026-11-16T10:45:00.000Z"
  }
}
```

Oldest first. `summary` is `null` until one exists (section 5).

> **Showing the screenshot:** use `imageUrl`, **not** `blobUrl`. The blob container is private, so `blobUrl` will not open in a browser. `imageUrl` is served by the API and needs the login:
> `<img src="{BASE_URL}{imageUrl}">` works when the session cookie is sent (same site, or CORS with credentials). Otherwise fetch it with the `Authorization` header and use `URL.createObjectURL(await res.blob())`.

### 4.2 All my alerts

`GET {BASE_URL}/api/screen-alerts?interviewId={optional}&limit=50`

- `limit`: 1–200 (default 50). Newest first. Interviewers see their own; admins see all.
- **Response:** same shape as 4.1, with `summary: null`.

### 4.3 Screenshot image

`GET {BASE_URL}/api/screen-alerts/{alertId}/image`

- **Response 200:** the raw image (`image/png`, `image/jpeg`, `image/webp` or `image/gif`).
- **Errors:** `403` (not your interview) · `404`.

---

## 5. AI summary of the alerts

An AI agent writes a **100–120 word** summary of an interview's stored alerts. It reads only the **text** of the alerts. Screenshots and blob URLs are never sent to the model. The summary is saved against the interview.

It is created **automatically when the interview ends** (if there were HIGH alerts), and you can also trigger it manually.

### 5.1 Get the summary

`GET {BASE_URL}/api/interviews/{interviewId}/summary`

**Response 200**

```json
{
  "summary": {
    "interviewId": "7K2M-9QXT-4HVB",
    "userId": "cm3user001",
    "summary": "During this interview, the monitoring system raised three high-priority screen alerts over roughly seven minutes. All three involved AI assistant applications ... A reviewer should watch the recording and examine the stored screenshots.",
    "wordCount": 110,
    "alertCount": 3,
    "createdAt": "2026-11-16T10:45:00.000Z",
    "updatedAt": "2026-11-16T10:45:00.000Z"
  }
}
```

`"summary": null` means none exists yet (no alerts, or it has not been generated).

### 5.2 Create or refresh the summary

`POST {BASE_URL}/api/interviews/{interviewId}/summary`

- **Payload:** none. **Response 200:** same shape as 5.1. It replaces any earlier summary.
- **Errors:** `409 no_alerts` (nothing stored) · `503 ai_not_configured` · `504 ai_timeout` · `502 ai_unavailable / ai_invalid_response`.

Call this when the user presses "Regenerate", or if the automatic summary failed.

---

## 6. Recordings

Only the interviewer records (the candidate's video comes through LiveKit). Upload goes in numbered chunks, then you finish it.

### 6.1 Start

`POST {BASE_URL}/api/recordings` — Auth: user

```json
{ "interviewId": "7K2M-9QXT-4HVB", "contentType": "video/webm" }
```

`contentType` is `video/webm` (default) or `video/mp4`. **Response 201:** `{ "recording": { ...RecordingDTO } }` (see 6.5).

### 6.2 Upload a chunk

`PUT {BASE_URL}/api/recordings/{recordingId}/chunks/{index}` — Auth: user

- **Payload:** the **raw bytes** of the chunk as the body (not JSON), up to **16 MB**. `index` = 0, 1, 2 … (0–49999).
- Re-sending an index replaces that chunk, so retries are safe.
- **Response:** `204` (no body). **Errors:** `409 recording_closed` · `413` · `400` (empty chunk).

### 6.3 Finish

`POST {BASE_URL}/api/recordings/{recordingId}/complete` — Auth: user

```json
{ "chunkCount": 12, "durationSeconds": 1800 }
```

`chunkCount` = how many chunks you uploaded (numbered 0..n-1). `durationSeconds` is optional. **Response 200:** `{ "recording": { ... } }` with `status: "READY"`. Calling it twice is harmless. **Error:** `400 chunks_missing` with the missing indexes in `details.missing`.

### 6.4 List, get and play

- `GET {BASE_URL}/api/recordings?interviewId={optional}&limit=50` → `{ "recordings": [ ... ] }`, newest first.
- `GET {BASE_URL}/api/recordings/{recordingId}` → `{ "recording": { ... } }`.
- `GET {BASE_URL}/api/recordings/{recordingId}/video` → the video stream (supports `Range`, so seeking works). Use it as `<video src="{BASE_URL}{videoUrl}">` with the session cookie.

### 6.5 RecordingDTO

```json
{
  "id": "cm3rec001",
  "interviewId": "7K2M-9QXT-4HVB",
  "interviewTitle": "Backend Engineer Round 1",
  "candidateName": "Priya Sharma",
  "status": "READY",
  "contentType": "video/webm",
  "sizeBytes": 73400320,
  "durationSeconds": 1800,
  "startedAt": "2026-11-16T10:00:00.000Z",
  "endedAt": "2026-11-16T10:30:00.000Z",
  "createdAt": "2026-11-16T10:00:00.000Z",
  "videoUrl": "/api/recordings/cm3rec001/video"
}
```

`status` is `RECORDING`, `READY` or `FAILED`. `videoUrl` is `null` unless `READY`.

---

## 7. The meeting report (one call for everything stored)

`GET {BASE_URL}/api/interviews/{interviewId}/report`

- **Auth:** user (owner or admin)
- **Payload:** none

Use this for the "meeting details" page. It returns the interview, its recordings, the stored HIGH alerts with screenshot links, the AI summary and the event timeline.

**Response 200**

```json
{
  "interview": {
    "id": "7K2M-9QXT-4HVB",
    "title": "Backend Engineer Round 1",
    "interviewerId": "cm3user001",
    "candidateName": "Priya Sharma",
    "status": "COMPLETED",
    "livekitRoomName": "interview-7K2M-9QXT-4HVB",
    "scheduledAt": null,
    "startedAt": "2026-11-16T10:00:00.000Z",
    "endedAt": "2026-11-16T10:30:00.000Z",
    "createdAt": "2026-11-16T09:50:00.000Z",
    "candidateJoinedAt": "2026-11-16T09:58:00.000Z",
    "monitoringStatus": null
  },
  "recordings": [ { "id": "cm3rec001", "status": "READY", "videoUrl": "/api/recordings/cm3rec001/video", "...": "see 6.5" } ],
  "screenAlerts": [ { "id": "cm3alert001", "alertLevel": "HIGH", "imageUrl": "/api/screen-alerts/cm3alert001/image", "blobUrl": "https://...", "...": "see 4.1" } ],
  "summary": { "summary": "During this interview ...", "wordCount": 110, "alertCount": 3, "...": "see 5.1" },
  "events": [
    {
      "id": "cm3evt001",
      "interviewId": "7K2M-9QXT-4HVB",
      "eventType": "screen_app_review",
      "message": "ChatGPT detected on screen. Review recommended.",
      "severity": "CRITICAL",
      "metadata": { "application": "ChatGPT", "category": "AI_ASSISTANT", "confidence": 0.96, "screenAlertId": "cm3alert001" },
      "createdAt": "2026-11-16T10:31:12.000Z"
    }
  ]
}
```

`summary` is `null` if none exists. `recordings` and `screenAlerts` are empty arrays when nothing is stored. To link a timeline event to its screenshot, match `events[].metadata.screenAlertId` with `screenAlerts[].id`.

---

## 8. Quick reference

| What | Method and path | Auth |
|---|---|---|
| Plan details | `GET /api/subscription/plan` | none |
| Start subscription | `POST /api/subscription/checkout` | user |
| Confirm payment | `POST /api/subscription/verify` | user |
| Subscription, limit, transactions | `GET /api/subscription/me` | user |
| Dashboard analytics | `GET /api/analytics/overview` | user |
| Analyse a screenshot | `POST /api/monitoring/screen-analysis` | user |
| Alerts of one interview | `GET /api/interviews/{id}/screen-alerts` | user |
| All my alerts | `GET /api/screen-alerts` | user |
| Alert screenshot | `GET /api/screen-alerts/{alertId}/image` | user |
| Get AI summary | `GET /api/interviews/{id}/summary` | user |
| Create or refresh summary | `POST /api/interviews/{id}/summary` | user |
| Meeting report (everything) | `GET /api/interviews/{id}/report` | user |
| Start / upload / finish recording | `POST /api/recordings` · `PUT /api/recordings/{id}/chunks/{n}` · `POST /api/recordings/{id}/complete` | user |
| List / get / play recordings | `GET /api/recordings` · `GET /api/recordings/{id}` · `GET /api/recordings/{id}/video` | user |

The full machine-readable list is also at `{BASE_URL}/docs` (Swagger) when docs are enabled on the server.
