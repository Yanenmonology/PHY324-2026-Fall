
from pathlib import Path
from inspect import signature
import json 
from collections import defaultdict

import matplotlib.pyplot as plt
import numpy as np
from scipy.optimize import curve_fit
import math

# Macro: density, visosity, bead density
MACRO = {"water": [1000, 0.001, 1.12], "glyco": [1261, 0.934, 2200]}
MODES = ("min", "Auto", "max", "cyl")
plot_keys = {"min": "min", "mid": "Auto", "max": "max", "cyl":"cyl"}
FIT_MODE = "default"



def build_plot_data(beads):
    """Group by liquid_type instead of relying on the bead-list order."""
    by_liquid = {"glyco": [], "water": []}
    for bead in beads:
        liquid = bead.liquid_type.strip().lower()
        if liquid == "glycol":
            liquid = "glyco"
        if liquid not in by_liquid:
            raise ValueError(f"Unknown liquid type: {bead.liquid_type}")
        by_liquid[liquid].append(bead)

    all_data = {}
    for liquid in ("glyco", "water"):
        selected = by_liquid[liquid]
        if not selected:
            raise ValueError(f"No unified beads found for {liquid}")
        selected.sort(key=lambda bead: bead.diameter)
        data = {}
        for attribute_mode, plot_mode in plot_keys.items():
            data[plot_mode] = {
                "group_ids": [], "radius_mm": [], "radius_unc_mm": [],
                "velocity_mm_s": [], "velocity_unc_mm_s": []
            }
            for bead in selected:
                data[plot_mode]["group_ids"].append(bead.group_id)
                data[plot_mode]["radius_mm"].append(bead.diameter / 2)
                data[plot_mode]["radius_unc_mm"].append(bead.diameter_unc / 2)
                data[plot_mode]["velocity_mm_s"].append(getattr(bead, f"v_{attribute_mode}"))
                data[plot_mode]["velocity_unc_mm_s"].append(getattr(bead, f"v_{attribute_mode}_unc"))
        all_data[liquid] = data
    return all_data["glyco"], all_data["water"]

def glyco_fit(r, a):
    return a * np.asarray(r, dtype=float)**2

def water_fit_wrapper(mode="default"):
    if mode == "linear":
        def water_func(r, a, b):
            return a * np.asarray(r, dtype=float) + b
    elif mode == "quad":
        def water_func(r, a):
            return a * np.asarray(r, dtype=float)**2
    elif mode == "default":
        def water_func(r, a):
            return a * np.sqrt(np.asarray(r, dtype=float))
    elif mode == "acu":
        def water_func(r, a, n):
            return a * (np.asarray(r, dtype=float) / 1.0)**n
    else:
        raise ValueError(f"Unknown fitting mode: {mode}")
    return water_func

def fit_curve(func, x, y, y_sig):
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    y_sig = np.asarray(y_sig, dtype=float)
    for values in (x, y, y_sig):
        if values.ndim != 1 or values.shape != x.shape:
            raise ValueError("x, y, and y_sig must be equal-length 1D arrays")
        if not np.all(np.isfinite(values)):
            raise ValueError("Fit inputs must be finite")
    if np.any(x <= 0) or np.any(y_sig <= 0):
        raise ValueError("Radii and velocity uncertainties must be positive")
    parameter_count = len(signature(func).parameters) - 1
    if len(x) <= parameter_count:
        raise ValueError("More data points than fitted parameters are required")
    popt, pcov = curve_fit(func, x, y, sigma=y_sig,
                           absolute_sigma=True, maxfev=10000)
    if not np.all(np.isfinite(pcov)):
        raise RuntimeError("Unable to estimate parameter covariance")
    u_fit = np.sqrt(np.diag(pcov))
    return popt, u_fit, pcov

def water_curve_fit(mode, x, y, y_sig, return_covariance=False):
    popt, u_fit, pcov = fit_curve(water_fit_wrapper(mode), x, y, y_sig)
    if return_covariance:
        return popt, u_fit, pcov
    return popt, u_fit

def fit_data(data, model_function):
    """Keep [parameter, uncertainty]; both entries are arrays for multi-parameter models."""
    fitted_data = {}
    parameter_covariance = {}
    for mode in MODES:
        group = data[mode]
        popt, u_fit, pcov = fit_curve(model_function, group["radius_mm"],
                                     group["velocity_mm_s"], group["velocity_unc_mm_s"])
        if len(popt) == 1:
            fitted_data[mode] = [float(popt[0]), float(u_fit[0])]
        else:
            fitted_data[mode] = [popt, u_fit]
        parameter_covariance[mode] = pcov
    return fitted_data, parameter_covariance


def model_metadata(mode):
    if mode == "linear":
        return ["a", "b"], ["s^{-1}", "mm/s"], "Fit: v = a r + b"
    if mode == "quad":
        return ["a"], ["(mm s)^{-1}"], "Fit: v = a r²"
    if mode == "default":
        return ["a"], ["mm^{1/2}/s"], "Fit: v = a √r"
    if mode == "acu":
        return ["a", "n"], ["mm/s at r = 1 mm", ""], "Fit: v = a (r / 1 mm)^{n}"
    raise ValueError(f"Unknown fitting mode: {mode}")

def plot_fits(data, fitted_data, model_function, output_path=None,
              parameter_label="parameter", parameter_unit="",
              fit_label="Fitted model", velocity_covariance=None,
              label="glyco", parameter_covariance=None, band_nsigma=1):
    if not np.isfinite(band_nsigma) or band_nsigma <= 0:
        raise ValueError("band_nsigma must be finite and positive")
    modes = list(MODES)
    if "Auto" not in data:
        modes[1] = "mid"
    fig, axes = plt.subplots(2, 4, figsize=(13.5, 7.5), sharex="col", sharey="row",
                             gridspec_kw={"height_ratios": [3, 1.4]},
                             layout="constrained")
    colors = {"min": "#47a814", "Auto": "#1B4ED0", "mid": "#187b89", "max": "#e1550f","cyl":"#c4153b"}
    for column, mode in enumerate(modes):
        group = data[mode]
        r = np.asarray(group["radius_mm"], dtype=float)
        ur = np.asarray(group["radius_unc_mm"], dtype=float)
        v = np.asarray(group["velocity_mm_s"], dtype=float)
        uv = np.asarray(group["velocity_unc_mm_s"], dtype=float)
        if len(fitted_data[mode]) != 2:
            raise ValueError(f"{mode}: expected [parameters, uncertainties]")
        parameters = np.atleast_1d(fitted_data[mode][0]).astype(float)
        uncertainties = np.atleast_1d(fitted_data[mode][1]).astype(float)
        if parameters.ndim != 1 or parameters.shape != uncertainties.shape:
            raise ValueError(f"{mode}: each parameter must have an uncertainty")
        count = len(parameters)
        dof = len(r) - count
        if dof <= 0:
            raise ValueError("More data points than fitted parameters are required")

        # Avoid r=0 for free-power models with negative fitted exponents.
        grid = np.linspace(0.85*np.min(r), 1.08*np.max(r), 300)
        fitted_curve = model_function(grid, *parameters)
        residual = v - model_function(r, *parameters)
        covariance = np.diag(uv**2)
        if velocity_covariance is not None:
            covariance = np.asarray(velocity_covariance[mode], dtype=float)
        chi2 = float(residual @ np.linalg.solve(covariance, residual))
        chi2_reduced = chi2 / dof

        pcov = np.diag(uncertainties**2)
        if parameter_covariance is not None:
            pcov = np.asarray(parameter_covariance[mode], dtype=float)
        if pcov.shape != (count, count) or not np.all(np.isfinite(pcov)):
            raise ValueError(f"{mode}: invalid parameter covariance matrix")
        jacobian = np.zeros((len(grid), count))
        for index in range(count):
            step = max(abs(parameters[index])*1e-5, 1e-8)
            plus = parameters.copy()
            minus = parameters.copy()
            plus[index] += step
            minus[index] -= step
            jacobian[:, index] = (model_function(grid, *plus)
                                  - model_function(grid, *minus))/(2*step)
        curve_variance = np.einsum("ij,jk,ik->i", jacobian, pcov, jacobian)
        curve_unc = np.sqrt(np.maximum(curve_variance, 0))

        names = parameter_label
        if isinstance(names, str):
            names = [names]
            if count > 1:
                names = []
                for index in range(count):
                    names.append(f"p{index}")
        units = parameter_unit
        if isinstance(units, str):
            units = [units]*count
        if len(names) != count or len(units) != count:
            raise ValueError("Supply a label and unit for each parameter")
        annotation = []
        for index in range(count):
            annotation.append(f"{names[index]} = {parameters[index]:.4g} ± {uncertainties[index]:.2g} {units[index]}")
        annotation.append(f"Reduced χ²= {chi2_reduced:.2f}")

        ax, residual_ax = axes[0, column], axes[1, column]
        ax.errorbar(r, v, xerr=ur, yerr=uv, fmt="o", capsize=3,
                    color=colors[mode], label="Unified groups (1σ)", zorder=3)
        ax.plot(grid, fitted_curve, color=colors[mode], label=fit_label)
        ax.fill_between(grid, fitted_curve-band_nsigma*curve_unc,
                        fitted_curve+band_nsigma*curve_unc, color=colors[mode],
                        alpha=.15, label=f"Fit ±{band_nsigma:g}σ")
        ax.set_title(f"{mode} wall correction")
        ax.text(.04, .95, "\n".join(annotation), transform=ax.transAxes,
                va="top", fontsize=9)
        ax.grid(alpha=.2)
        ax.legend(loc="lower right", fontsize=8)
        # These bars show measurement uncertainty, not fitted-residual covariance.
        residual_ax.errorbar(r, residual, yerr=np.sqrt(np.diag(covariance)),
                             fmt="o", capsize=3, color=colors[mode])
        residual_ax.axhline(0, color="0.35", linewidth=1)
        residual_ax.set_xlabel("Mean bead radius (mm)")
        residual_ax.grid(alpha=.2)
    axes[0, 0].set_ylabel("Corrected terminal velocity (mm/s)")
    axes[1, 0].set_ylabel("Data − fit (mm/s)")
    titles = {"glyco": "Teflon in Glyco", "water": "Nylon in Water"}
    fig.suptitle(f"{titles.get(label.lower(), label)}: fits to the unified size groups", fontsize=15)
    if output_path is not None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path, dpi=200)
    return fig, axes

def Reyn_eq(d, u_d, v, u_v, Rho, eta):
    '''
    d: meter, v: meter/second, Rho: kg/m^3 eta: Pa/S
    '''
    Re = Rho * v * d / eta
    u_Re = Re * math.sqrt(((u_v / v)**2+ (u_d / d)**2))
    return Re, u_Re

def Reynold_number_function(liq, data):
    fluid_density_kg_m3 = MACRO[liq][0]
    viscosity_Pa_s = MACRO[liq][1]
    Reynold = defaultdict(list)
    u_Reynold = defaultdict(list)
    for m in MODES:
        temp = data[m]
        re_list = []
        u_re_list = []
        for i in range(len(temp["group_ids"])):
            d_m = (temp["radius_mm"][i] / 1000) * 2
            u_d_m = temp["radius_unc_mm"][i] / 1000
            v_m_s = abs(temp["velocity_mm_s"][i]) / 1000
            u_v_m_s = abs(temp["velocity_unc_mm_s"][i]) / 1000
            Re, u_Re = Reyn_eq(d_m, u_d_m, v_m_s,u_v_m_s,fluid_density_kg_m3,viscosity_Pa_s)
            re_list.append(Re)
            u_re_list.append(u_Re)
        Reynold[m] = re_list
        u_Reynold[m]= u_re_list
    return Reynold, u_Reynold


def main(beads=None, fit_mode=FIT_MODE, output_dir=None, show=True):
    if beads is None:
        # Import only when running, so fitting functions can be used independently.
        from exp_class_v0 import Bead
        beads = Bead.Load_Beads()
    data_glyco, data_water = build_plot_data(beads)
    fitted_data_glyco, covariance_glyco = fit_data(data_glyco, glyco_fit)
    water_func = water_fit_wrapper(fit_mode)
    fitted_data_water, covariance_water = fit_data(data_water, water_func)
    water_labels, water_units, water_fit_label = model_metadata(fit_mode)
    glyco_path = None
    water_path = None
    if output_dir is not None:
        output_dir = Path(output_dir)
        glyco_path = output_dir / "glyco_fit.png"
        water_path = output_dir / f"water_fit_{fit_mode}.png"
    glyco_figure, _ = plot_fits(
        data_glyco, fitted_data_glyco, glyco_fit, output_path=glyco_path,
        parameter_label="a", parameter_unit="(mm s)⁻¹", fit_label="Fit: v = a r²",
        label="glyco", parameter_covariance=covariance_glyco)
    water_figure, _ = plot_fits(
        data_water, fitted_data_water, water_func, output_path=water_path,
        parameter_label=water_labels, parameter_unit=water_units,
        fit_label=water_fit_label, label="water", parameter_covariance=covariance_water)
    print("Glyco [parameters, uncertainties]:", fitted_data_glyco)
    print(f"Water ({fit_mode}) [parameters, uncertainties]:", fitted_data_water)
    if show:
        plt.show()
    
    g_Re, g_u_Re = Reynold_number_function("glyco",data_glyco)
    w_Re, w_u_Re = Reynold_number_function("water",data_water)
    return {
        "data_glyco": data_glyco, "data_water": data_water, "Re_glyco":g_Re, "u_Re_glyco": g_u_Re,
        "fitted_data_glyco": fitted_data_glyco, "fitted_data_water": fitted_data_water, "Re_water":w_Re,"u_Re_water":w_u_Re,
        "covariance_glyco": covariance_glyco, "covariance_water": covariance_water,
        "figures": (glyco_figure, water_figure)
    }

if __name__ == "__main__":
    summary = main(show=False)
    #test = json.dumps(summary)

    print("\n\n----------------------------[SUMMARY]----------------------------")
    print(f"glyco re data is: {summary['Re_glyco']}\n glyco re unc data is: {summary['u_Re_glyco']}\n\n")
    print("-------------------------------------------------------")
    print(f"water re data is: {summary['Re_water']}\n water re unc data is: {summary['u_Re_water']}")
    print("-------------------------------------------------------")

    # now the theoretical part:
    dlist= []
    vlist = []
    the_re = []
    the_liq_key = "data_water"
    the_liqq_key = "fitted_data_water"
    for i in range(5):
        temp_d = summary[the_liq_key]["cyl"]["radius_mm"][i] / 500
        temp_u_d =summary[the_liq_key]["cyl"]["radius_unc_mm"][i] / 500
        dlist.append(temp_d)
        temp_v = summary[the_liqq_key]["cyl"][0] / 1000
        temp_u_v = summary[the_liqq_key]["cyl"][1] / 1000
        vlist.append(temp_v)
        ree,u_ree = Reyn_eq(temp_d,temp_u_d,temp_v,temp_u_v,1261,0.934)
        the_re.append(ree)
    
    print(ree)

