"""Example: dependence between bead drops, without repeating range selections.

Requires NumPy and Matplotlib. Run without arguments for a SYNTHETIC example.

Real data:
  python bead_run_dependence.py --csv terminal_velocity.csv
Add a drop_order column giving the actual drop sequence within each bath.
If run_id really is that sequence, explicitly pass --run-id-is-order instead.
Optional columns: bath_id, u_diameter (independent standard uncertainty, mm).
Required columns: Liquid_type, run_id, group_id, diameter, velocity, u_total,
                  u_calibration. Blank velocities are skipped.

Covariance model (a sensitivity assumption, not estimated from the trail):
  S_ij = delta_ij*u_ind_i**2 + u_fluid**2*rho**abs(order_i-order_j)+ velocity_i*velocity_j*relative_common**2
Environmental/calibration cross-terms are zero between distinct baths.The AR(1) model assumes equally spaced drop steps and a stationary disturbance.
It‘s unsuitable for irregular waiting times without a different time model.

u_ind = sqrt(u_total**2-u_calibration**2): this treats the original fit and
selection budget as independent ACROSS runs. 
"""

import argparse
import csv
from collections import defaultdict
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")  # Save plots; no selection window is opened.
import matplotlib.pyplot as plt


def nonnegative(value, name):
    value = float(value)
    if not np.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and nonnegative plz")
    return value


def covariance_matrix(runs, u_fluid, rho, relative_common):
    """Rows/columns use the input run order; diagonals are variances."""
    u_fluid = nonnegative(u_fluid, "u_fluid")
    relative_common = nonnegative(relative_common, "relative_common")
    if not np.isfinite(rho) or not 0 <= rho < 1:
        raise ValueError("This example requires 0 <= rho < 1")
    matrix = np.zeros((len(runs), len(runs)))
    for i, run_i in enumerate(runs):
        for j, run_j in enumerate(runs):
            if i == j:
                matrix[i, j] += run_i["u_ind"] ** 2
            if run_i["bath"] == run_j["bath"]:
                lag = abs(run_i["order"] - run_j["order"])
                matrix[i, j] += u_fluid ** 2 * rho ** lag
                matrix[i, j] += (
                    run_i["velocity"] * run_j["velocity"]
                    * relative_common ** 2
                )
    return matrix


def mean_uncertainty(matrix):
    """Var(mean) = a.T @ S @ a, where every a_i = 1/n."""
    n = len(matrix)
    if n == 0:
        raise ValueError("Cannot average an empty group")
    weights = np.full(n, 1 / n)
    variance = float(weights @ matrix @ weights)
    return np.sqrt(max(0.0, variance))


def read_runs(path, use_run_id, default_u_diameter):
    runs = []
    with Path(path).expanduser().open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"Liquid_type", "run_id", "group_id", "diameter", "velocity",
                    "u_total", "u_calibration"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Missing CSV columns: {sorted(missing)}")
        if not use_run_id and "drop_order" not in reader.fieldnames:
            raise ValueError("Add actual drop_order values, or use --run-id-is-order "
                             "ONLY if run_id records the actual drop sequence.")
        for row in reader:
            if not row["velocity"].strip():
                continue
            velocity = float(row["velocity"])
            diameter = float(row["diameter"])
            u_total = nonnegative(row["u_total"], "u_total")
            u_cal = nonnegative(row["u_calibration"], "u_calibration")
            if u_cal > u_total + 1e-10 * max(1.0, u_total):
                raise ValueError("u_calibration exceeds u_total")
            order_value = float(row["run_id"] if use_run_id else row["drop_order"])
            if not np.isfinite(order_value) or order_value < 1 or not order_value.is_integer():
                raise ValueError("Drop order must be a positive integer")
            if not np.isfinite(velocity) or not np.isfinite(diameter) or diameter <= 0:
                raise ValueError("Velocity must be finite; diameter must be positive")
            diameter_unc = default_u_diameter
            if row.get("u_diameter", "").strip():
                diameter_unc = nonnegative(row["u_diameter"], "u_diameter")
            liquid = row["Liquid_type"]
            bath_id = row.get("bath_id", "").strip() or "one_assumed_bath"
            runs.append({
                "liquid": liquid, "run_id": row["run_id"],
                "group": row["group_id"], "bath": (liquid, bath_id),
                "order": int(order_value), "diameter": diameter,
                "u_diameter": diameter_unc, "velocity": velocity,
                "original_u_calibration": u_cal,
                "u_ind": np.sqrt(max(0.0, u_total ** 2 - u_cal ** 2)),
            })
    if not runs:
        raise ValueError("No populated run velocities found")
    seen = set()
    for run in runs:
        identity = (run["bath"], run["order"])
        if identity in seen:
            raise ValueError("Repeated drop order within one bath; check bath_id/order")
        seen.add(identity)
    return runs


def demo_runs():
    rng = np.random.default_rng(20)
    diameters = [3.02, 2.98, 3.01, 2.99, 3.00]
    u_fluid, rho = 0.25, 0.7
    h = rng.normal(0, u_fluid)  # Stationary initial disturbance.
    runs = []
    for i, diameter in enumerate(diameters):
        if i > 0:
            h = rho * h + rng.normal(0, u_fluid * np.sqrt(1 - rho ** 2))
        runs.append({
            "liquid": "SYNTHETIC", "run_id": str(i + 1), "group": "example",
            "bath": ("SYNTHETIC", "demo"), "order": i + 1,
            "diameter": diameter, "u_diameter": 0.01,
            "velocity": 5 + 2 * (diameter - 3) + h + rng.normal(0, 0.05),
            "u_ind": 0.05,
        })
    return runs


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def analyze(runs, output, u_fluid, rhos, relative_common, u_d_common):
    output.mkdir(parents=True, exist_ok=True)
    groups = defaultdict(list)
    for run in runs:
        groups[(run["liquid"], run["group"])].append(run)
    summaries, diagnostic_rows = [], []
    for group_number, (key, group) in enumerate(groups.items(), start=1):
        group.sort(key=lambda run: (run["bath"], run["order"]))
        n = len(group)
        diameter = np.array([run["diameter"] for run in group])
        velocity = np.array([run["velocity"] for run in group])
        centered_d = diameter - diameter.mean()
        baseline = np.full(n, velocity.mean())
        baseline_kind = "group_mean_no_diameter_correction"
        # A local line is a descriptive approximation, not a physical law.
        if n >= 4 and np.ptp(diameter) > 1e-12:
            design = np.column_stack((np.ones(n), centered_d))
            beta = np.linalg.lstsq(design, velocity, rcond=None)[0]
            baseline = design @ beta
            baseline_kind = "local_OLS_line_ignores_diameter_uncertainty"
        residual = velocity - baseline
        for i, run in enumerate(group):
            diagnostic_rows.append({
                "Liquid_type": run["liquid"], "group_id": run["group"],
                "run_id": run["run_id"], "bath_id": run["bath"][1],
                "drop_order": run["order"], "diameter": run["diameter"],
                "velocity": run["velocity"], "baseline_velocity": baseline[i],
                "residual_velocity": residual[i], "baseline_model": baseline_kind,
            })
        diameter_matrix = np.zeros((n, n))
        known_diameter_unc = all(run["u_diameter"] is not None for run in group)
        if known_diameter_unc:
            for i, run_i in enumerate(group):
                for j, run_j in enumerate(group):
                    if i == j:
                        diameter_matrix[i, j] += run_i["u_diameter"] ** 2
                    # Here the diameter instrument is assumed shared over the whole group.
                    diameter_matrix[i, j] += u_d_common ** 2
        for scenario_number, rho in enumerate(rhos, start=1):
            matrix = covariance_matrix(group, u_fluid, rho, relative_common)
            independent_matrix = covariance_matrix(group, 0, 0, 0)
            diagonal_only = np.diag(np.diag(matrix))
            summaries.append({
                "Liquid_type": key[0], "group_id": key[1], "n_runs": n,
                "mean_diameter": float(diameter.mean()),
                "sd_diameter_real_spread": float(np.std(diameter, ddof=1)) if n > 1 else "",
                "u_mean_diameter_measurement": mean_uncertainty(diameter_matrix) if known_diameter_unc else "",
                "mean_velocity": float(velocity.mean()),
                "sd_velocity_observed": float(np.std(velocity, ddof=1)) if n > 1 else "",
                "assumed_u_fluid": u_fluid, "assumed_rho": rho,
                "assumed_relative_common": relative_common,
                "u_mean_existing_independent_budget": mean_uncertainty(independent_matrix),
                "u_mean_same_variances_ignoring_covariance": mean_uncertainty(diagonal_only),
                "u_mean_with_assumed_covariance": mean_uncertainty(matrix),
            })
            labels = [f"{run['bath'][1]}:run_{run['run_id']}" for run in group]
            covariance_rows = []
            for i, label in enumerate(labels):
                row = {"row_run": label}
                for j, column in enumerate(labels):
                    row[column] = matrix[i, j]
                covariance_rows.append(row)
            write_csv(output / f"covariance_group_{group_number}_scenario_{scenario_number}.csv", covariance_rows)
        fig, axes = plt.subplots(1, 3, figsize=(13, 4))
        axes[0].scatter(diameter, velocity)
        sorted_indices = np.argsort(diameter)
        axes[0].plot(diameter[sorted_indices], baseline[sorted_indices], color="0.5")
        axes[0].set(xlabel="Diameter (mm)", ylabel="Velocity (mm/s)")
        baths = defaultdict(list)
        for i, run in enumerate(group):
            baths[run["bath"]].append(i)
        for bath, indices in baths.items():
            order = [group[i]["order"] for i in indices]
            axes[1].plot(order, residual[indices], "o-", label=bath[1])
            axes[2].plot(order, diameter[indices], "o-", label=bath[1])
        axes[1].axhline(0, color="0.5", linestyle="--")
        axes[1].set(xlabel="Actual drop order", ylabel="Baseline residual (mm/s)")
        axes[2].set(xlabel="Actual drop order", ylabel="Diameter (mm)")
        axes[1].legend(fontsize=8)
        fig.suptitle(f"{key[0]} | size group {key[1]} | descriptive only")
        fig.tight_layout()
        fig.savefig(output / f"diagnostic_group_{group_number}.png", dpi=160)
        plt.close(fig)
    write_csv(output / "group_uncertainty_scenarios.csv", summaries)
    write_csv(output / "run_diagnostics.csv", diagnostic_rows)
    return summaries


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", type=Path, help="One terminal-velocity row per run")
    parser.add_argument("--run-id-is-order", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("dependence_results"))
    parser.add_argument("--u-fluid", type=float, help="ASSUMED additional fluid disturbance SD, mm/s")
    parser.add_argument("--rho", type=float, nargs="+", default=[0, 0.3, 0.6, 0.9])
    parser.add_argument("--relative-common", type=float,
                        help="ASSUMED shared relative velocity calibration SD per bath")
    parser.add_argument("--diameter-unc", type=float,
                        help="Independent diameter standard uncertainty, mm; row u_diameter overrides")
    parser.add_argument("--u-diameter-common", type=float, default=0,
                        help="Shared additive diameter standard uncertainty, mm")
    args = parser.parse_args()
    for name in ("relative_common", "u_diameter_common", "diameter_unc", "u_fluid"):
        value = getattr(args, name)
        if value is not None:
            nonnegative(value, name)
    for rho in args.rho:
        if not np.isfinite(rho) or not 0 <= rho < 1:
            parser.error("rho must satisfy 0 <= rho < 1")
    if args.csv:
        runs = read_runs(args.csv, args.run_id_is_order, args.diameter_unc)
        if args.relative_common is None and any(run["original_u_calibration"] > 0 for run in runs):
            parser.error("Your CSV has nonzero calibration uncertainty. Specify "
                         "--relative-common for the shared-calibration scenario; "
                         "the script cannot infer its covariance structure.")
        u_fluid = args.u_fluid if args.u_fluid is not None else 0.0
        print("REAL INPUT. Fluid covariance is a sensitivity assumption, not an estimate.")
        print("Without bath_id, each liquid is assumed to use one continuous bath.")
        if args.u_fluid is None:
            print("No u_fluid supplied: environmental covariance is omitted, not proven absent.")
    else:
        runs = demo_runs()
        u_fluid = args.u_fluid if args.u_fluid is not None else 0.25
        print("SYNTHETIC EXAMPLE ONLY: assumed fluid SD =", u_fluid, "mm/s")
    relative_common = args.relative_common if args.relative_common is not None else 0.0
    summaries = analyze(runs, args.output, u_fluid, args.rho,
                        relative_common, args.u_diameter_common)
    for row in summaries:
        print(f"{row['Liquid_type']} group {row['group_id']}, rho={row['assumed_rho']}: "
              f"mean v={row['mean_velocity']:.5g}, "
              f"u_mean={row['u_mean_with_assumed_covariance']:.5g} mm/s")
    print("Saved plots, diagnostics, group scenarios and covariance matrices to", args.output)
    print("No causal conclusion or stationary-fluid bias correction is made.")


if __name__ == "__main__":
    main()
