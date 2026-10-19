"""
Calibration for camera set up with ChArUco board
-Intrinsic/single camera calibration: move ChArUco slowly, and pause then move again.
-Extrinsic/full setup
"""
from __future__ import annotations
import glob
from pathlib import Path
import cv2 as cv
import numpy as np
import function_bus as fb

def grey_scaled(frame):
    return cv.cvtColor(frame, cv.COLOR_BGR2GRAY) if frame.ndim == 3 else frame

def detect(det, board, grey, min_corners=8):
    ch_corners, ch_ids, _, _ = det.detectBoard(grey)
    if ch_ids is None or len(ch_ids)<min_corners:
        return None
    obj, img = board.matchImagePoints()