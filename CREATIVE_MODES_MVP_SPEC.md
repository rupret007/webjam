# Creative Modes MVP Spec

## Goal
Ship one shared cross-discipline collaboration layer in WebJam without splitting into multiple products.

## MVP Scope
- Add mode metadata schema (mode key, label, default template, default goal, quick help, critique prompts).
- Add mode/template/goal controls in main entry flow.
- Persist room context in storage (`mode_key`, `template_name`, `session_goal`, `review_state`).
- Add shared session canvas panel:
  - pinned artifacts/references
  - live notes
  - review state (`draft`, `review`, `final`)
  - mode-specific critique prompts

## Non-Goals (for MVP)
- Dedicated visual canvas drawing engine
- Real-time collaborative text sync over network
- Mode-specific custom UIs per discipline

## Data Model (MVP)
- `collaboration_rooms`
- `collaboration_artifacts`
- `collaboration_notes`

## UX Flow
1. User selects mode.
2. User sets template + goal.
3. User launches Jamulus/Webex and collaborates.
4. Team uses session canvas to attach references and record outcomes.
5. Session context is retained for next run.

## Success Signals
- Room context persists and reloads.
- At least one artifact and note can be saved per session.
- Users can switch modes without losing architecture consistency.
