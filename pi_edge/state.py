"""
Shared mutable state for Pi edge application.

All variables are module-level globals mutated by concurrent threads.
Only simple types (bool, float, list, None) are used — no lock needed for reads
because Python's GIL ensures atomic assignment of these types.
"""

# Camera + YOLO state
running: bool = False
latest_frame = None          # numpy array from camera_loop
annotated_frame = None       # numpy array with YOLO bounding boxes drawn
detected_crops: list = []    # list of crop dicts ready for submission

# Stability tracker state (written by yolo_worker, read by cloud_submit_loop)
stable_progress: float = 0.0   # 0.0 → 1.0 as best detection holds still for STABILITY_WINDOW_S
should_submit: bool = False     # True when stable; cleared after submission is dispatched
