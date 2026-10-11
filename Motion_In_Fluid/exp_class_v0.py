from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.widgets import Button, CheckButtons, SpanSelector
from scipy.optimize import least_squares
import math
from scipy import constants as C

#density in unit of g/cm^3
RHO_WATER = 1.00
RHO_GLYCO = 1.26
RHO_NYLON = 1.12
RHO_TEFLON = 2.2
#Viscosity in unit of Posie, at 25 degree
ETA_WATER = 0.01
ETA_GLYCO = 9.34

MACRO = {"water":[1,0.01,1.12],"glyco":[1260,0.934,2200]}

@dataclass(frozen=True)
class Bead:
    '''
    v_min v_mid and v_max with their respective total uncertainty are refered to the wall coefficients
    '''
    liquid_type: str
    group_id: int
    v_min: float
    v_min_unc: float
    v_mid: float
    v_mid_unc: float
    v_max: float
    v_max_unc: float
    v_cyl: float
    v_cyl_unc: float
    diameter: float
    diameter_unc: float
    

    @staticmethod
    def _resolve_file(filename, data_dir=None):
        if data_dir is not None:
            path = Path(data_dir).expanduser() / filename
            if not path.is_file():
                raise FileNotFoundError(path)
            return path
        roots = (Path.cwd(), Path(__file__).resolve().parent, Path(__file__).resolve().parent.parent)
        for root in roots:
            path = root / filename
            if path.is_file():
                return path
        raise FileNotFoundError(f"Cannot find {filename}; supply data_dir explicitly")
    
    @classmethod
    def Load_Beads(cls,name="groups_wall_summary.csv", dir="Latest_result"):
        path = cls._resolve_file(name,dir)
        #it will be {(water,1):{"min":[v,v_err],"mid":[v,v_err],"max":[v,v_err]},(water,2):......}
        groups = defaultdict(lambda:defaultdict(list))
        beads_buffer = []
        with path.open(encoding="utf-8-sig", newline="") as handle:

            for row in csv.DictReader(handle):
                k = f"{row['Liquid_type']}-{row['group_id']}"
                groups[k][row["wall_mode"]].append(row["velocity_wall_corrected"])
                groups[k][row["wall_mode"]].append(row["u_total"])
                groups[k]["dia"].append(row["diameter_mean_mm"])
                groups[k]["dia"].append(row["diameter_mean_unc_mm"])

            for k in groups.keys():
                liquid,grp_id = k.split("-")
                beads_buffer.append(
                    cls(
                        liquid_type = liquid, group_id=int(grp_id),
                        v_min = float(groups[k]["min"][0]), v_min_unc = float(groups[k]["min"][1]),
                        v_mid = float(groups[k]["Auto"][0]), v_mid_unc = float(groups[k]["Auto"][1]),
                        v_max = float(groups[k]["max"][0]), v_max_unc = float(groups[k]["max"][1]),
                        v_cyl = float(groups[k]["cyl"][0]),v_cyl_unc = float(groups[k]["cyl"][1]),
                        diameter = float(groups[k]["dia"][0]),diameter_unc = float(groups[k]["dia"][1])
                    )
                )
        return beads_buffer
    
    @staticmethod
    def plot_radius_velocity(unified_beads, liquid_type,mode="all",ax=None):
        fields = {
            "min": ("v_min", "v_min_unc"),
            "auto": ("v_mid", "v_mid_unc"),
            "max": ("v_max", "v_max_unc"),
            "cyl":("v_cyl", "v_cyl_unc")
        }
        styles = {
            "min": ("o", "#a45c13"),
            "auto": ("s", "#187b89"),
            "max": ("^", "#7161a6"),
            "cyl":("*","#c4153b")
        }
        mode = mode.lower()
        if mode == "all":
            modes = ["min", "auto", "max", "cyl"]
        elif mode in fields:
            modes = [mode]
        else:
            raise ValueError("mode must be min, Auto, max, or all")
        selected = []
        for bead in unified_beads:
            if bead.liquid_type == liquid_type:
                selected.append(bead)
        if not selected:
            raise ValueError(f"No unified beads for {liquid_type}")
        selected.sort(key=lambda bead: bead.diameter)
        if ax is None:
            fig, ax = plt.subplots(figsize=(7, 5))
        else:
            fig = ax.figure
        for wall_mode in modes:
            velocity_field, uncertainty_field = fields[wall_mode]
            marker, color = styles[wall_mode]
            radii = []
            radius_uncertainties = []
            velocities = []
            velocity_uncertainties = []
            for bead in selected:
                radii.append(bead.diameter / 2)
                radius_uncertainties.append(bead.diameter_unc / 2)
                velocities.append(getattr(bead, velocity_field))
                velocity_uncertainties.append(
                    getattr(bead, uncertainty_field)
                )
            label = "Auto" if wall_mode == "auto" else wall_mode
            ax.errorbar(
                radii,
                velocities,
                xerr=radius_uncertainties,
                yerr=velocity_uncertainties,
                fmt=marker,
                color=color,
                linestyle="none",
                capsize=3,
                label=f"{label} wall width",
            )
        ax.set_xlabel("Bead radius (mm)")
        ax.set_ylabel("Wall-corrected terminal velocity (mm/s)")
        ax.set_title(f"Bead in {liquid_type} Terminal Velocity (mm/s) vs radius(mm)")
        ax.grid(alpha=0.3)
        ax.legend()
        return fig, ax

    @staticmethod
    def _theoretical_v_term_water(radius_mm, *, viscosity_unit="Pa*s"):
        """
        radius_mm: scalar or array of radii in mm
        MACRO["water"] densities: g/cm^3
        viscosity_unit: "Pa*s" or "poise"
        Returns: predicted free-fluid terminal velocity in mm/s
        """
        import numpy as np
        from scipy.optimize import brentq

        rho_water = float(MACRO["water"][0]) * 1000
        rho_bead = float(MACRO["water"][-1]) * 1000
        eta = float(MACRO["water"][1])

        if viscosity_unit == "poise":
            eta *= 0.1
        elif viscosity_unit != "Pa*s":
            raise ValueError("viscosity_unit must be 'Pa*s' or 'poise'")

        if not np.all(np.isfinite([rho_water, rho_bead, eta, C.G])):
            raise ValueError("Fluid properties and gravity must be finite")
        if eta <= 0 or rho_water <= 0 or C.g <= 0:
            raise ValueError("Viscosity, water density, and gravity must be positive")
        if rho_bead <= rho_water:
            raise ValueError("This function assumes a bead denser than water")

        radii = np.asarray(radius_mm, dtype=float)

        if np.any(~np.isfinite(radii)) or np.any(radii < 0):
            raise ValueError("Radii must be finite and nonnegative")

        velocities = np.empty_like(radii)

        for index in np.ndindex(radii.shape):
            r = float(radii[index]) / 1000  # convert mm -> m
            if r == 0:
                velocities[index] = 0
                continue
            # Stokes prediction provides an upper bracket for the solver.
            v_stokes = (
                2 * (rho_bead - rho_water) * C.g * r**2
                / (9 * eta)
            )
            def balance(v):
                Re = rho_water * (2 * r) * v / eta
                # Equivalent to Cd = max(Schiller–Naumann Cd, 0.44).
                # This form also handles v=0 without dividing by Re.
                drag_factor = max(
                    1 + 0.15 * Re**0.687,
                    0.44 * Re / 24,
                )
                return v * drag_factor - v_stokes
            v = brentq(balance, 0, v_stokes, xtol=1e-14)
            if rho_water * (2 * r) * v / eta >= 2e5:
                raise ValueError("Predicted Re is outside this model's intended range")
            velocities[index] = v * 1000  # unit conversion: m/s -> mm/s

        return velocities.item() if radii.ndim == 0 else velocities