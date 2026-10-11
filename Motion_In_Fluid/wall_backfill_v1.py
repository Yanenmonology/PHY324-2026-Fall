""" Test all wall correction, back fitting use the exisiting selections sessions for each beads.
Run:
    python wall_backfill.py --runs terminal_velocity.csv \
        --selections all_selection.csv --output-dir wall_results

Outputs:
    runs_wall_corrections.csv: one row/run, explicit min/Auto/max/cyl columns.
    groups_wall_summary.csv: one row/liquid/size group/wall mode.
    selections_wall_corrections.csv: same saved ranges with added wall fields.

Default u_d=0.01 mm is for viner caliper measure;
u_W=0.5 mm is the supplied standard uncertainty from ruler measures.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

# Units in mm
WALL_SIDES = {"glyco": (92.0, 93.0), "water": (93.0, 94.0)}
WALL_WIDTHS = {
    liquid: {"min": min(a, b), "Auto": (a+b)/2, "max": max(a, b),
             "cyl": 2*math.sqrt(a*b/math.pi)}
    for liquid, (a, b) in WALL_SIDES.items()
}
WALL_MODES = ("min", "Auto", "max", "cyl")

def number(value, name, *, nonnegative=False, positive=False):
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite number; got {value!r}") from exc
    if not math.isfinite(result) or (nonnegative and result < 0) or (positive and result <= 0):
        raise ValueError(f"Invalid {name}: {value!r}")
    return result


def equivalent_cylinder(side_a, side_b, side_a_unc=0.5, side_b_unc=0.5, cov_sides=0.0):
    a = number(side_a, "side_a", positive=True)
    b = number(side_b, "side_b", positive=True)
    ua = number(side_a_unc, "side_a_unc", nonnegative=True)
    ub = number(side_b_unc, "side_b_unc", nonnegative=True)
    cov = number(cov_sides, "cov_sides")
    if abs(cov) > ua*ub*(1+1e-12):
        raise ValueError("Require abs(Cov(a,b)) <= side_a_unc * side_b_unc")
    diameter = 2*math.sqrt(a*b/math.pi)
    da, db = diameter/(2*a), diameter/(2*b)
    variance = (da*ua)**2 + (db*ub)**2 + 2*da*db*cov
    return diameter, math.sqrt(max(0.0, variance))


def wall_geometry(liquid, mode, wall_size_unc=0.5):
    if liquid not in WALL_SIDES or mode not in WALL_MODES:
        raise ValueError(f"Unsupported wall geometry {(liquid, mode)!r}")
    uw = number(wall_size_unc, "wall_size_unc", nonnegative=True)
    if mode == "cyl":
        return equivalent_cylinder(*WALL_SIDES[liquid], uw, uw)
    return WALL_WIDTHS[liquid][mode], uw


def optional_number(row, key, default, *, nonnegative=False):
    value = row.get(key)
    return number(default if value is None or str(value).strip() == "" else value,
                  key, nonnegative=nonnegative)


def identifier(value, name):
    result = number(value, name)
    if not result.is_integer():
        raise ValueError(f"{name} must be an integer")
    return int(result)


def read_csv(path):
    with Path(path).expanduser().open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames
        if not headers or len(set(headers)) != len(headers):
            raise ValueError(f"Missing or duplicate CSV headers: {path}")
        rows = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError(f"Malformed CSV row: {path}")
    if not rows:
        raise ValueError(f"Empty CSV: {path}")
    return rows


def wall_coefficient(diameter, width, diameter_unc=0.01, width_unc=0.5,
                     model_rel_unc=0.0, cov_diameter_width=0.0):
    d = number(diameter, "diameter", positive=True)
    w = number(width, "wall width", positive=True)
    ud = number(diameter_unc, "diameter_unc", nonnegative=True)
    uw = number(width_unc, "wall_size_unc", nonnegative=True)
    rm = number(model_rel_unc, "wall_model_rel_unc", nonnegative=True)
    cov = number(cov_diameter_width, "cov_diameter_wall_size")
    if d >= w:
        raise ValueError("Require diameter < wall width")
    if abs(cov) > ud * uw * (1+1e-12):
        raise ValueError("Require abs(Cov(d,W)) <= diameter_unc * wall_size_unc")
    q = d/w
    denominator = 1-2.104*q+2.089*q**3
    if denominator <= 0:
        raise ValueError("Wall denominator must be positive")
    c = 1/denominator
    derivative = (2.104-6.267*q*q)*c*c
    uq2 = (ud/w)**2 + (d*uw/w**2)**2 - 2*d*cov/w**3
    uc = math.sqrt(derivative**2*max(0.0, uq2)+(c*rm)**2)
    return c, uc


def run_identity(row):
    liquid = str(row["Liquid_type"]).strip()
    if liquid not in WALL_WIDTHS:
        raise ValueError(f"Unsupported liquid {liquid!r}; use glyco or water")
    return liquid, identifier(row["run_id"], "run_id")


def raw_run_budget(row):
    """Return raw v, total, independent part, calibration part, and test note."""
    velocity = number(row["velocity"], "velocity")
    total = number(row["u_total"], "u_total", nonnegative=True)
    if row.get("u_calibration") not in (None, ""):
        calibration = number(row["u_calibration"], "u_calibration", nonnegative=True)
        basis = "Saved u_total and saved u_calibration"
    else:
        scale = optional_number(row, "scale_rel_unc", 0.0, nonnegative=True)
        fps = optional_number(row, "fps_unc", 0.0, nonnegative=True)
        calibration = abs(velocity)*math.hypot(scale, fps)
        basis = "Saved u_total; calibration reconstructed from supplied relative errors (missing errors assumed zero)"
    variance = total**2-calibration**2
    if variance < -1e-10*max(total**2, 1.0):
        raise ValueError("Raw calibration uncertainty exceeds raw total uncertainty")
    if row.get("u_fit") not in (None, "") and row.get("u_selection") not in (None, ""):
        fit = number(row["u_fit"], "u_fit", nonnegative=True)
        sensitivity = number(row["u_selection"], "u_selection", nonnegative=True)
        if not np.isclose(total**2, fit**2+sensitivity**2+calibration**2, rtol=1e-8, atol=1e-12):
            raise ValueError("Saved u_total disagrees with fit/selection/calibration components")
    return velocity, total, math.sqrt(max(0.0, variance)), calibration, basis


def correct_runs(rows, *, diameter_unc=0.01, wall_size_unc=0.5, wall_model_rel_unc=0.0):
    """Return wide run rows; original velocity and UNC REMAIN RAW!!!"""
    corrected = []
    seen = set()
    for row in rows:
        if row.get("status", "accepted").strip().lower() not in {"accepted", ""}:
            continue
        liquid, run_id = run_identity(row)
        if (liquid, run_id) in seen:
            raise ValueError(f"Duplicate run {(liquid,run_id)}")
        seen.add((liquid, run_id))
        group_id = identifier(row["group_id"], "group_id")
        d = number(row["diameter"], "diameter", positive=True)
        ud = optional_number(row, "diameter_unc", diameter_unc, nonnegative=True)
        # The revised side budget supersedes stale uncertainty in saved CSVs.
        uw = number(wall_size_unc, "wall_size_unc", nonnegative=True)
        rm = optional_number(row, "wall_model_rel_unc", wall_model_rel_unc, nonnegative=True)
        cov = optional_number(row, "cov_diameter_wall_size", 0.0)
        v, ut, independent, calibration, basis = raw_run_budget(row)
        result = dict(row)
        if row.get("wall_size_unc") not in (None, ""):
            result["saved_wall_size_unc_mm"] = row["wall_size_unc"]
        result.update(Liquid_type=liquid, run_id=run_id, group_id=group_id,
                      diameter=d, diameter_unc=ud, wall_size_unc=uw,
                      wall_model_rel_unc=rm, cov_diameter_wall_size=cov,
                      velocity=v, u_total=ut, u_calibration=calibration,
                      u_independent_raw=independent, wall_budget_basis=basis,
                      wall_side_a_mm=WALL_SIDES[liquid][0],
                      wall_side_b_mm=WALL_SIDES[liquid][1],
                      wall_side_a_unc_mm=uw, wall_side_b_unc_mm=uw,
                      cov_wall_sides_mm2=0.0,
                      wall_formula="1/(1-2.104*(d/W)+2.089*(d/W)^3)")
        for mode in WALL_MODES:
            width, effective_uw = wall_geometry(liquid, mode, uw)
            c, uc = wall_coefficient(d, width, ud, effective_uw, rm, cov)
            result[f"wall_size_mm_{mode}"] = width
            result[f"wall_size_unc_mm_{mode}"] = effective_uw
            result[f"wall_geometry_{mode}"] = (
                "equal_area_cylinder_approximation" if mode == "cyl" else "width_scenario")
            result[f"wall_coef_{mode}"] = c
            result[f"wall_cor_unc_{mode}"] = uc
            result[f"velocity_wall_corrected_{mode}"] = c*v
            result[f"u_independent_corrected_{mode}"] = c*independent
            result[f"u_calibration_corrected_{mode}"] = c*calibration
            result[f"u_wall_{mode}"] = abs(v)*uc
            result[f"u_total_corrected_{mode}"] = math.hypot(c*ut, v*uc)
        corrected.append(result)
    if not corrected:
        raise ValueError("No accepted runs available")
    return corrected


def unify_groups(corrected_runs):
    """One final mean and standard uncertainty per liquid/size/mode. bruv finally...."""
    groups = defaultdict(list)
    for row in corrected_runs:
        groups[(row["Liquid_type"], row["group_id"])].append(row)
    output = []
    for (liquid, group_id), rows in sorted(groups.items()):
        n = len(rows)
        run_ids = [row["run_id"] for row in rows]
        d = np.array([row["diameter"] for row in rows], dtype=float)
        ud = np.array([row["diameter_unc"] for row in rows], dtype=float)
        raw_v = np.array([row["velocity"] for row in rows], dtype=float)
        for mode in WALL_MODES:
            velocities = np.array([row[f"velocity_wall_corrected_{mode}"] for row in rows])
            independent = np.array([row[f"u_independent_corrected_{mode}"] for row in rows])
            walls = np.array([row[f"u_wall_{mode}"] for row in rows])
            calibration = np.array([row[f"u_calibration_corrected_{mode}"] for row in rows])
            sample_variance = float(np.var(velocities, ddof=1)) if n > 1 else 0.0
            tau2 = max(0.0, sample_variance-float(np.mean(independent**2))) if n > 1 else 0.0
            u_from_run = float(np.sqrt(np.sum(independent**2))/n)
            u_from_scatter = math.sqrt(tau2/n)
            u_random = math.hypot(u_from_run, u_from_scatter)
            u_wall = float(np.mean(walls))
            u_cal = float(np.mean(calibration))
            notes = ["Equal-weight mean; other run errors assumed independents",
                     "Full wall and calibration budgets use fully correlated conservative bounds across runs",
                     "Diameter mean uncertainty includes independent measurement errors only!!!!!!",
                     "Wall modes are separate widthscenarios; do not average them!!!!! Maybe fine?"]
            if mode == "cyl":
                notes.append("Equal-area cylinder is a geometry approximation; independent side errors propagated to D")
            if n == 1:
                notes.append("Single run: between-run scatter not assessed, need to check between run covariance.")
            if np.any(velocities < 0) and np.any(velocities > 0):
                notes.append("Mixed slope signs: check whether signed v should be avged")
            output.append({
                "Liquid_type": liquid, "group_id": group_id, "wall_mode": mode,
                "wall_size_mm": WALL_WIDTHS[liquid][mode], "n_runs": n,
                "wall_size_unc_mm": rows[0][f"wall_size_unc_mm_{mode}"],
                "run_ids": json.dumps(run_ids),
                "diameter_mean_mm": float(np.mean(d)),
                "diameter_mean_unc_mm": float(np.sqrt(np.sum(ud**2))/n),
                "raw_velocity_mean_mm_s": float(np.mean(raw_v)),
                "wall_coef_mean": float(np.mean([row[f"wall_coef_{mode}"] for row in rows])),
                "wall_coef_min_among_runs": min(row[f"wall_coef_{mode}"] for row in rows),
                "wall_coef_max_among_runs": max(row[f"wall_coef_{mode}"] for row in rows),
                "velocity_wall_corrected": float(np.mean(velocities)),
                "u_from_run_uncertainties": u_from_run,
                "sample_variance_corrected": sample_variance,
                "tau_between_runs_mm_s": math.sqrt(tau2),
                "u_from_between_run_scatter": u_from_scatter,
                "u_random_mean": u_random,
                "u_shared_wall": u_wall, "u_shared_calibration": u_cal,
                "u_total": math.sqrt(u_random**2+u_wall**2+u_cal**2),
                "notes": "; ".join(notes),
            })
    return output


def correct_selections(selection_rows, corrected_runs):
    lookup = {(row["Liquid_type"], row["run_id"]): row for row in corrected_runs}
    output = []
    seen = set()
    for row in selection_rows:
        key = run_identity(row)
        if key not in lookup:
            continue  # Excluded runs are omitted consistently with run outputs.
        run = lookup[key]
        selection_id = row.get("selection_id", row.get("id"))
        if not selection_id or (*key, selection_id) in seen:
            raise ValueError(f"Missing or duplicate selection ID for {key}")
        seen.add((*key, selection_id))
        if (identifier(row["group_id"], "group_id") != run["group_id"]
                or not np.isclose(number(row["diameter"], "diameter"), run["diameter"], rtol=0, atol=1e-9)):
            raise ValueError(f"Selection metadata differs from run summary for {key}")
        v = number(row["velocity"], "selection velocity")
        ufit = number(row["u_fit"], "selection u_fit", nonnegative=True)
        rel_scale = optional_number(run, "scale_rel_unc", 0.0, nonnegative=True)
        rel_fps = optional_number(run, "fps_unc", 0.0, nonnegative=True)
        rel_cal = math.hypot(rel_scale, rel_fps)
        if rel_cal == 0 and run["u_calibration"] > 0:
            if run["velocity"] == 0:
                raise ValueError("Cannot recover selection calibration from a zero run velocity")
            rel_cal = run["u_calibration"]/abs(run["velocity"])
        result = dict(row)
        result["wall_budget_basis"] = "Saved range fit + calibration + wall; excludes run-level range sensitivity"
        for mode in WALL_MODES:
            c, uc = run[f"wall_coef_{mode}"], run[f"wall_cor_unc_{mode}"]
            result[f"wall_size_mm_{mode}"] = run[f"wall_size_mm_{mode}"]
            result[f"wall_size_unc_mm_{mode}"] = run[f"wall_size_unc_mm_{mode}"]
            result[f"wall_geometry_{mode}"] = run[f"wall_geometry_{mode}"]
            result[f"wall_coef_{mode}"] = c
            result[f"wall_cor_unc_{mode}"] = uc
            result[f"velocity_wall_corrected_{mode}"] = c*v
            result[f"u_fit_corrected_{mode}"] = c*ufit
            result[f"u_wall_{mode}"] = abs(v)*uc
            result[f"u_calibration_corrected_{mode}"] = abs(c*v)*rel_cal
            result[f"u_fit_and_wall_{mode}"] = math.hypot(c*ufit, v*uc)
            result[f"u_total_corrected_{mode}"] = math.sqrt((c*ufit)**2+(v*uc)**2+(c*v*rel_cal)**2)
        output.append(result)
    if not output:
        raise ValueError("No saved selections match accepted run summaries")
    return output


def check_chosen_selections(selection_rows, run_rows):
    """Check chosen-range means/IDs against saved runs; not refitting the data!"""
    chosen = defaultdict(list)
    for row in selection_rows:
        flag = str(row.get("including_in_average", "")).strip().lower()
        if flag not in {"true", "false", "1", "0", "yes", "no"}:
            raise ValueError("Selection file requires explicit including_in_average flags")
        if flag in {"true", "1", "yes"}:
            chosen[run_identity(row)].append(row)
    for run in run_rows:
        key = run_identity(run)
        selections = chosen.get(key, [])
        if not selections:
            raise ValueError(f"No chosen ranges for saved run {key}")
        mean = np.mean([number(row["velocity"], "selection velocity") for row in selections])
        if not np.isclose(mean, run["velocity"], rtol=1e-8, atol=1e-10):
            raise ValueError(f"Chosen ranges disagree with saved run velocity for {key}; use matching CSV files")
        ids = run.get("selection_ids")
        if ids:
            try:
                saved_ids = json.loads(ids)
            except json.JSONDecodeError:
                saved_ids = [value.strip() for value in ids.split(";")]
            if not isinstance(saved_ids, list) or set(saved_ids) != {row["selection_id"] for row in selections}:
                raise ValueError(f"Chosen selection IDs disagree for {key}")


def write_csv(path, rows):
    target = Path(path)
    # Union preserves original column order even when callers use heterogeneous dicts.
    fields = list(dict.fromkeys(key for row in rows for key in row))
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return target


def backfill_wall_corrections(runs_csv, output_dir="wall_results", *, selections_csv=None,
                              diameter_unc=0.01, wall_size_unc=0.5, wall_model_rel_unc=0.0):
    for value, name in ((diameter_unc, "diameter_unc"), (wall_size_unc, "wall_size_unc"),
                        (wall_model_rel_unc, "wall_model_rel_unc")):
        number(value, name, nonnegative=True)
    runs = correct_runs(read_csv(runs_csv), diameter_unc=diameter_unc,
                        wall_size_unc=wall_size_unc, wall_model_rel_unc=wall_model_rel_unc)
    groups = unify_groups(runs)
    selections = None
    if selections_csv is not None:
        saved = read_csv(selections_csv)
        check_chosen_selections(saved, runs)
        selections = correct_selections(saved, runs)
    out = Path(output_dir).expanduser()
    paths = {"runs": out/"runs_wall_corrections.csv", "groups": out/"groups_wall_summary.csv"}
    if selections is not None:
        paths["selections"] = out/"selections_wall_corrections.csv"
    inputs = {Path(runs_csv).expanduser().resolve()}
    if selections_csv is not None:
        inputs.add(Path(selections_csv).expanduser().resolve())
    if any(path.resolve() in inputs for path in paths.values()):
        raise ValueError("Output paths must differ from source CSV paths")
    # Finish validation/calculation before writing any output.
    write_csv(paths["runs"], runs)
    write_csv(paths["groups"], groups)
    if selections is not None:
        write_csv(paths["selections"], selections)
    return runs, groups, paths


def main(argv=None):
    parser = argparse.ArgumentParser(description="Apply min/Auto/max/cyl wall corrections to saved velocities; no reselection")

    parser.add_argument("--runs", default="terminal_velocity.csv")
    parser.add_argument("--selections", help="Optional all_selection.csv;")
    parser.add_argument("--output-dir", default="wall_results")
    parser.add_argument("--diameter-unc", type=float, default=0.01)
    parser.add_argument("--wall-size-unc", type=float, default=0.5)
    parser.add_argument("--wall-model-rel-unc", type=float, default=0.0)
    args = parser.parse_args(argv)
    try:
        runs, groups, paths = backfill_wall_corrections(
            args.runs, args.output_dir, selections_csv=args.selections,
            diameter_unc=args.diameter_unc, wall_size_unc=args.wall_size_unc,
            wall_model_rel_unc=args.wall_model_rel_unc)
    except (ValueError, KeyError, OSError) as exc:
        parser.exit(2, f"Error: {exc}\n")
    print(f"Processed {len(runs)} accepted runs; {len(groups)} liquid/size/mode summaries.")
    print("min/Auto/max refer to width; cyl uses the characteristic diameter.")
    print("liquid-size-mode-W(mm)-mean C-corrected v +/- u (mm/s)")
    for row in groups:
        print(f"{row['Liquid_type']:6}  {row['group_id']:4}  {row['wall_mode']:4}  "
              f"{row['wall_size_mm']:6.2f}  {row['wall_coef_mean']:7.5f}  "
              f"{row['velocity_wall_corrected']:10.5f} +/- {row['u_total']:.5f}")
    for key, path in paths.items():
        print(f"{key}: {path}")
    return 0


if __name__ == "__main__":
    #fingers cross, knock on wood
    sys.exit(main())
