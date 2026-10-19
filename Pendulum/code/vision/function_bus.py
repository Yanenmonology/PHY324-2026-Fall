"""
For shared functions that may used in different python files.
"""
from __future__ import annotations
import csv
import json
import yaml
from pathlib import Path

import numpy as np
import cv2 as cv

aruco = cv.aruco

# configuration department:

def load_vision_config(config_file_name = "vision_config.yaml"):
    cwd = Path(__file__).parent.parent.parent.resolve()
    resource_folder = cwd / "resource" / config_file_name
    p = Path(resource_folder)
    if not p.exists():
        raise FileNotFoundError(f"File {p} does not exits, route error")
    with open(p) as f:
        return yaml.safe_load(f)

def load_cam_names(cfg):
    return [c["name"] for c in cfg["cameras"]]

def load_cam_cfg(cfg,name):
    for c in cfg["cameras"]:
        if c["namd"] == name:
            return c
    raise SystemExit(f"Camera {name} not in the vision_config.ymal, but we have{load_cam_names(cfg)}")

# Aruco department:

def aruco_dict(name):
    return aruco.getPredefinedDictionary(getattr(aruco, name))

#need update after the board is available
def make_charuco(cfg):
    pass

#this function use the example of dectection without calibration in opencv
def charuco_dectector(board):
    params = aruco.DetectorParameters()
    params.cornerRefinementMethod = aruco.CORNER_REFINE_SUBPIX
    ch_params = aruco.CharucoParameters()
    ch_params.tryRefineMarkers = True
    return aruco.CharucoDetector(board, ch_params, params)

def marker_dectrctor(cfg:dict):
    params = aruco.DetectorParameters()
    params.cornerRefinementMethod = aruco.CORNER_REFINE_SUBPIX
    params.cornerRefinementMaxIterations = 50
    params.cornerRefinementMinAccuracy = 0.01
    params.cornerRefinementWinSize = 4 # up to the actual board, need update
    return aruco.ArucoDetector(aruco_dict(cfg["MBS_marker"]["dictionary"]))

def tracked_markers(cfg):
    marker = cfg["MBS_marker"]
    ids = [int(i) for i in marker["ids"]]
    sizes = [marker["size_mm"]/1000] * len(ids)
    veil = [False] * len(ids)
    anchor = cfg.get("anchor") or {}
    for i in anchor.get("ids", []) or []:
        ids.append(int(i))
        sizes.append(anchor.get("size_mm", marker["size_mm"])/1000)
        veil.append(True)
    return np.array(ids), np.array(sizes), np.array(veil)

def marker_object_point(size_m):
    #order: TL, TR,BR,BL, z =0
    h = size_m / 2.0
    return np.array([-h, h, 0], [h, h, 0], [h, -h, 0], [-h, -h, 0], dtype=np.float64)

#IO department:

#Camera department:

#timestamp department: