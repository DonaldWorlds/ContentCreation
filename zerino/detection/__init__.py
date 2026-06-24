"""Event-driven highlight detection (Phase 0.5+).

Windows-side batch stage. IMPORT BOUNDARY: no torch / cv2 / OCR imports at module
top — the Mac side and the live capture daemon must import this package without GPU
deps installed. Heavy libs load lazily inside adapter methods only.

Authoritative decisions: DETECTION_DECISIONS.md. Interfaces: PROJECT_REVIEW.md §E.
"""
