from exp_class_v0 import Bead
import matplotlib.pyplot as plt
import numpy as np
import math
import scipy.constants as C
from scipy.optimize import curve_fit



MACRO = {"water":[1,0.01,1.12],"glyco":[1260,0.934,2200]}
MODES = ("min", "Auto", "max","cyl")

def _theoretical_v_term(liq, radius):

    bead_density = MACRO[liq][-1]
    solution_density = MACRO[liq][0]
    solution_visco = MACRO[liq][1]

    #mass = 4 * math.pi *(radius**3) * bead_density / 3
    #return (mass * C.g)/ (6 * math.pi * solution_visco * radius)
    return (
            2 * (bead_density - solution_density)
            * C.g * radius**2
            / (9 * solution_visco)
        )

bs = Bead.Load_Beads()
'''Glyco'''
fig, axes = plt.subplots(
    1, 2,
    figsize=(12, 5),
    layout="constrained",
)
radius_min_mm = bs[0].diameter / 2
radius_max_mm = bs[4].diameter / 2
radius_curve_mm = np.linspace(
    radius_min_mm,
    radius_max_mm,
    300,
)

# Convert mm -> m before calculating theoretical velocity.
velocity_curve_m_s = _theoretical_v_term(
    bs[0].liquid_type,
    radius_curve_mm/1000,
)

# Convert m/s -> mm/s to match the experimental axes.
axes[0].plot(
    radius_curve_mm,
    velocity_curve_m_s * 1000,
    color="black",
    linestyle="--",
    linewidth=1.5,
    label="Theoretical prediction",
)

'''Water'''
water_min_mm = bs[5].diameter / 2
water_max_mm = bs[-1].diameter / 2
radius_curve_mm = np.linspace(
    water_min_mm,
    water_max_mm,
    300,
)
velocity_curve_mm_s = Bead._theoretical_v_term_water(
    radius_curve_mm,
    viscosity_unit="Pa*s",
)
axes[1].plot(
    radius_curve_mm,
    velocity_curve_mm_s,
    "k--",
    label="Schiller–Naumann prediction",
)

Bead.plot_radius_velocity(
    bs,
    liquid_type="glyco",
    mode="all",
    ax=axes[0],
)
Bead.plot_radius_velocity(
    bs,
    liquid_type="water",
    mode="all",
    ax=axes[1],
)

plt.show()