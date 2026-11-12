"""Reserved for the AI features (face/gaze/object detection, LLM analysis, ...).

Keep model code here and run it outside the request cycle (a LiveKit Agents worker
or a task queue). Findings should be stored through
``app.services.monitoring.record_event`` so they appear on the existing timeline."""
