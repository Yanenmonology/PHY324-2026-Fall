"""Bead tracking: saved linear fits, range sensitivity, and uncertainty.
Units: seconds, millimetres, mm/s. All supplied uncertainties are ONE-SIGMA.
"""
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
import math as m

W_UNC = 0.01  # Assumed one-sigma wall-size uncertainty, mm; override per bead.
G_Ws = [92, 92.5, 93]
W_Ws = [93, 93.5, 94]
G_W = 92.5 #0.5*(width+length)
W_W = 93.5 #0.5*(width+length)

def _characteristic_wall_diameter(a1,a2, unc=0.5):
    A = a1*a2
    unc_A = m.sqrt((unc/a1)**2 + (unc/a2)**2)
    unc_D = m.sqrt((m.pi * unc_A**2)/ (4 * A))
    return m.sqrt(A/m.pi) *2, unc_D

def _Correction(wall_size, diameter):
    wall_size = float(wall_size)
    diameter = float(diameter)
    if not np.isfinite(wall_size) or not np.isfinite(diameter) or not 0 < diameter < wall_size:
        raise ValueError("Require finite 0 < diameter < wall_size (mm)")
    ratio = diameter/wall_size
    denominator = 1-2.104*ratio+2.089*(ratio**3)
    if denominator <= 0:
        raise ValueError("Wall-correction denominator must be positive")
    return 1/denominator

def _wall_size(liquid, mode="Auto"):
    Valid = ["min","max","Auto","cyl"]
    modes = {"min": 0, "auto": 1, "max": 2}
    sizes = {"glyco": G_Ws, "water": W_Ws}
    if mode in Valid:
        if mode == "cyl":
            wall_size, wall_size_unc = float(_characteristic_wall_diameter(sorted(sizes[liquid])[0],sorted(sizes[liquid])[-1]))
        else:
            wall_size = float(sorted(sizes[liquid])[modes[mode.lower()]])
            wall_size_unc = 0.01
    if not isinstance(mode, str) or mode.lower() not in modes:
        raise ValueError("Wall mode must be Auto, min, or max")
    if liquid not in sizes:
        raise ValueError("Wall correction liquid type must be glyco or water")
    return wall_size

def _Wall_affect(l, dia, mode="Auto"):
    return _Correction(_wall_size(l, mode), dia)

@dataclass
class RangeFit:
    selection_id: str
    choosen_t0: float
    choosen_t1: float
    measured_t0_pt: float
    measured_t1_point: float
    n_points: int
    velocity: float
    u_fit: float
    interception: float
    t_reference: float
    x_reference: float
    method: str
    uncertainty_model: str
    residual_rms: float
    r_squared: float | None
    reduced_chi_2: float | None
    residual_lag1: float | None
    acceleration: float | None
    u_acceleration: float | None
    block_velocity: tuple[float, ...]
    point_indices: tuple[int, ...]
    data_hash: str
    wall_mode: str
    wall_coef: float
    wall_cor_unc: float
    velocity_wall_corrected: float
    velocity_wall_cor_unc: float
    x_sigma: tuple[float, ...] | None = None
    t_sigma: tuple[float, ...] = ()
    hac_lags: int | None = None
    timing_model: str = "supplied"
    excluded_zero: bool = True
    wall_size_mm: float | None = None
    wall_input_hash: str = ""
    u_calibration: float = 0.0
    velocity_wall_cor_total_unc: float = 0.0

    @property
    def speed(self):
        return abs(self.velocity)

    def prediction(self, time):
        # Centre the calculation to avoid cancellation for large timestamps.
        return self.x_reference + self.velocity * (np.asarray(time) - self.t_reference)


@dataclass(frozen=True)
class Velocity_Summary:
    selection_ids: tuple[str, ...]
    velocity: float
    u_fit: float
    u_selection: float
    u_calibration: float
    u_total: float
    overlap_domain: bool
    test: tuple[str, ...]
    wall_mode: str = "Auto"
    wall_coef: float = 1.0
    wall_cor_unc: float = 0.0
    velocity_wall_corrected: float = 0.0
    u_fit_wall_corrected: float = 0.0
    u_selection_wall_corrected: float = 0.0
    u_calibration_wall_corrected: float = 0.0
    u_wall: float = 0.0
    velocity_wall_cor_unc: float = 0.0

    @property
    def speed(self):
        return abs(self.velocity)

    def __iter__(self):
        return iter((self.velocity, self.u_total))


@dataclass(frozen=True)
class Delta_x_Estimate:
    """First-order propagation for a DERIVED displacement d = v * time_interval."""
    delta_t: float
    time_unc: float
    velocity: float
    v_unc: float
    displacement: float
    u_from_t: float
    u_from_v: float
    cov_term_variance: float
    u_displacement: float
    source_selection_ids: tuple[str, ...]
    uncertainty_basis: str
    test: tuple[str, ...]


def _nonnegative(value, name):
    if not np.isscalar(value):
        raise ValueError(f"{name} must be a finite nonnegative scalar")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite nonnegative scalar") from exc
    if not np.isfinite(result) or result < 0:
        raise ValueError(f"{name} must be a finite nonnegative scalar")
    return result


def _linear_fit(t, x, sigma=None):
    """Need to return centred coefficients, covariance, residuals, and design matrix."""
    t, x = np.asarray(t, dtype=float), np.asarray(x, dtype=float)
    if t.ndim != 1 or x.ndim != 1 or len(t) != len(x) or len(t) < 3:
        raise ValueError("Linear fitting needs at least three paired time/position values")
    if not np.all(np.isfinite(t)) or not np.all(np.isfinite(x)):
        raise ValueError("Linear fitting requires finite data")
    if sigma is not None:
        sigma = np.asarray(sigma, dtype=float)
        if sigma.ndim == 0:
            sigma = np.full(len(t), float(sigma))
        if sigma.shape != t.shape or np.any(~np.isfinite(sigma)) or np.any(sigma <= 0):
            raise ValueError("sigma must be positive, finite, and scalar or match the data")
    reference = float(np.mean(t))
    scale = float(np.ptp(t))
    if scale <= 0:
        raise ValueError("The range needs at least three distinct timestamps")
    z = (t - reference) / scale
    design = np.column_stack((np.ones(len(t)), z))
    weight = np.ones(len(t)) if sigma is None else 1 / sigma
    matrix = design * weight[:, None]
    beta, _, rank, _ = np.linalg.lstsq(matrix, x * weight, rcond=None)
    if rank < 2:
        raise ValueError("The linear fit is singular")
    residual = x - design @ beta
    cov = np.linalg.inv(matrix.T @ matrix)
    if sigma is None:
        cov *= float(residual @ residual / (len(t) - 2))
    return beta, cov, residual, design, reference, scale


def _hac_cov(jacobian, residual, lags):
    """for newey-West covariance with Bartlett weights and small-sample correction."""
    jacobian, residual = np.asarray(jacobian, dtype=float), np.asarray(residual, dtype=float)
    if jacobian.ndim != 2 or residual.shape != (len(jacobian),):
        raise ValueError("HAC needs an n-by-p Jacobian and an n-element residual array")
    n, p = jacobian.shape
    if n <= p or np.linalg.matrix_rank(jacobian) < p:
        raise ValueError("HAC requires a full-rank Jacobian and n > p")
    if not np.all(np.isfinite(jacobian)) or not np.all(np.isfinite(residual)):
        raise ValueError("HAC inputs must be finite")
    if isinstance(lags, (bool, np.bool_)) or not isinstance(lags, (int, np.integer)) or not 0 <= lags < n:
        raise ValueError("lags must be an integer from 0 to n-1")
    bread = np.linalg.inv(jacobian.T @ jacobian)
    scores = jacobian * residual[:, None]
    meat = scores.T @ scores
    for lag in range(1, lags + 1):
        cross = scores[lag:].T @ scores[:-lag]
        meat += (1 - lag / (lags + 1)) * (cross + cross.T)
    return bread @ meat @ bread * n / (n - p)


@dataclass
class Bead:
    Liquid_type: str
    run_id: int
    group_id: int
    pos_h: float
    pos_w: float
    diameter: float
    time_unc: float | list[float]
    fps_unc: float = 0.0  # Relative one-sigma frame-rate uncertainty u_f/f.
    position: list[float] = field(default_factory=list)
    time_point: list[float] = field(default_factory=list)
    fit: tuple | None = field(default=None, repr=False)
    v_err: float | None = None
    position_unc: float | list[float] | None = None
    fps: float | None = None
    timing_model: str = "supplied"
    excluded_zero: bool = True
    scale_rel_unc: float = 0.0
    diameter_unc: float = 0.01  # Assumed one-sigma measurement uncertainty, mm.
    wall_size_unc: float = W_UNC
    wall_size_mm: float | None = None  # Optional effective width, mm.
    wall_model_rel_unc: float = 0.0  # User-assessed relative uncertainty of C.
    cov_diameter_wall_size: float = 0.0  # mm^2; zero assumes independence.
    selections: list[RangeFit] = field(default_factory=list, init=False)
    summary: Velocity_Summary | None = field(default=None, init=False)
    current_fit: RangeFit | None = field(default=None, init=False, repr=False)
    _next_selection: int = field(default=1, init=False, repr=False)

    def __post_init__(self):
        self.scale_rel_unc = _nonnegative(self.scale_rel_unc, "scale_rel_unc")
        self.fps_unc = _nonnegative(self.fps_unc, "fps_unc")
        self._wall_inputs("Auto")
        if not isinstance(self.excluded_zero, (bool, np.bool_)):
            raise ValueError("excluded_zero must be a boolean")
        if self.timing_model not in {"supplied", "timestamps", "uniform_frame", "one_frame"}:
            raise ValueError("Unknown timing_model")
        if self.fps is not None and (not np.isfinite(self.fps) or self.fps <= 0):
            raise ValueError("fps must be positive and finite")

    def _wall_inputs(self, mode):
        preset = _wall_size(self.Liquid_type, mode)
        width = preset if self.wall_size_mm is None else float(self.wall_size_mm)
        diameter = float(self.diameter)
        _Correction(width, diameter)
        ud = _nonnegative(self.diameter_unc, "diameter_unc")
        uw = _nonnegative(self.wall_size_unc, "wall_size_unc")
        um = _nonnegative(self.wall_model_rel_unc, "wall_model_rel_unc")
        covariance = float(self.cov_diameter_wall_size)
        if not np.isfinite(covariance) or abs(covariance) > ud * uw * (1 + 1e-12):
            raise ValueError("Require abs(Cov(d,W)) <= diameter_unc * wall_size_unc")
        return diameter, width, ud, uw, um, covariance

    def _wall_hash(self, mode):
        # Include calibration settings so saved total uncertainties cannot go stale.
        values = (self.Liquid_type, *self._wall_inputs(mode),
                  self.scale_rel_unc, self.fps_unc)
        return hashlib.sha256(json.dumps(values, allow_nan=False).encode()).hexdigest()

    def _wall_fields(self, velocity, u_fit, mode):
        d, width, ud, uw, um, covariance = self._wall_inputs(mode)
        q = d / width
        coefficient = _Correction(width, d)
        derivative = (2.104 - 6.267*q*q) * coefficient**2
        uq2 = (ud/width)**2 + (d*uw/width**2)**2 - 2*d*covariance/width**3
        uc = float(np.sqrt(derivative**2 * max(0.0, uq2) + (coefficient*um)**2))
        ucal = abs(velocity) * np.hypot(
            _nonnegative(self.scale_rel_unc, "scale_rel_unc"),
            _nonnegative(self.fps_unc, "fps_unc"))
        corrected_unc = float(np.hypot(coefficient*u_fit, velocity*uc))
        return dict(
            wall_coef=float(coefficient), wall_cor_unc=uc,
            wall_size_mm=width, wall_input_hash=self._wall_hash(mode),
            velocity_wall_corrected=float(coefficient*velocity),
            velocity_wall_cor_unc=corrected_unc,
            u_calibration=float(ucal),
            velocity_wall_cor_total_unc=float(np.hypot(corrected_unc, coefficient*ucal)),
        )

    def _check_wall_inputs(self, selections):
        for selection in selections:
            if selection.wall_input_hash != self._wall_hash(selection.wall_mode):
                raise ValueError("Geometry or calibration changed since fitting; clear and refit selections")

    @property
    def key(self):
        return (self.Liquid_type, self.run_id)

    @property
    def frame_interval(self):
        return None if self.fps is None else 1 / self.fps

    @classmethod
    def from_lines(cls, line, *, timing_model="one_frame", **kwargs):
        '''
        Note: data dir need to be checked
        '''
        fps = float(line["fps"])
        if not np.isfinite(fps) or fps <= 0:
            raise ValueError("CSV fps must be positive and finite")
        factors = {"timestamps": 0.0, "uniform_frame": 1 / np.sqrt(12), "one_frame": 1.0}
        if timing_model == "supplied":
            if "time_unc" not in kwargs:
                raise ValueError("timing_model='supplied' requires time_unc")
            time_unc = kwargs.pop("time_unc")
        elif timing_model in factors:
            if "time_unc" in kwargs:
                raise ValueError("Use timing_model='supplied' when passing time_unc")
            time_unc = factors[timing_model] / fps
        else:
            raise ValueError("Unknown timing_model")
        return cls(
            Liquid_type=line["liquid_type"], run_id=int(line["trail_number"]),
            group_id=int(line["label_group"]), pos_h=float(line["h(mm)"]),
            pos_w=float(line["w(mm)"]), diameter=float(line["bead_size(mm)"]),
            time_unc=time_unc, fps=fps,
            timing_model=timing_model, **kwargs,
        )

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
    def create_beads(cls, data_dir=None, *, timing_model="one_frame", **kwargs):
        path = cls._resolve_file("general_bead_statistic.csv", data_dir)
        with path.open(encoding="utf-8-sig", newline="") as handle:
            beads_buffer = []
            for row in csv.DictReader(handle):
                beads_buffer.append(cls.from_lines(row, timing_model=timing_model, **kwargs))
            return beads_buffer

    @classmethod
    def load_pos(cls, liquid_type, data_dir=None):
        path = cls._resolve_file(f"{liquid_type}_summary_data.csv", data_dir)
        data = defaultdict(lambda: ([], []))
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                positions, times = data[(liquid_type, int(row["Trail_id"]))]
                positions.append(float(row["Position(mm)"]))
                times.append(float(row["Time(sec)"]))
        return dict(data)

    def _install_data(self, positions, times):
        self.position, self.time_point = list(positions), list(times)
        self.clear_selections()

    def load_position_data(self, data_dir=None):
        data = self.load_pos(self.Liquid_type, data_dir)
        if self.key not in data:
            raise KeyError(f"No position data for {self.key}")
        self._install_data(*data[self.key])
        return self

    @classmethod
    def load_all_pos(cls, liq_material, beads_mattrix, data_dir=None):
        data = cls.load_pos(liq_material, data_dir)
        missing = []
        for bead in beads_mattrix:
            if bead.Liquid_type == liq_material:
                if bead.key in data:
                    bead._install_data(*data[bead.key])
                else:
                    missing.append(bead.key)
        return missing

    def _array(self):
        t, x = np.asarray(self.time_point, dtype=float), np.asarray(self.position, dtype=float)
        if t.ndim != 1 or x.ndim != 1 or len(t) != len(x) or not len(t):
            raise ValueError("Load equal-length, nonempty time and position arrays first")
        return t, x

    def _fingerprint(self):
        t, x = self._array()
        digest = hashlib.sha256()
        digest.update(np.asarray(x, dtype="<f8").tobytes())
        digest.update(np.asarray(t, dtype="<f8").tobytes())
        return digest.hexdigest()

    @staticmethod
    def _sigma(value, n, indices, name, *, positive=False):
        array = np.asarray(value, dtype=float)
        if array.ndim == 0:
            array = np.full(len(indices), float(array))
        elif array.ndim == 1 and len(array) == n:
            array = array[indices]
        else:
            raise ValueError(f"{name} must be scalar or match the full data length ({n})")
        invalid = array <= 0 if positive else array < 0
        if np.any(~np.isfinite(array)) or np.any(invalid):
            raise ValueError(f"{name} must be finite and {'positive' if positive else 'nonnegative'}")
        return array

    def _calculate_fit(self, t0, t1, *, nBlocks=5, method="auto", uncertainty="iid", hac_lags=None, W_mode="Auto"):
        if not np.isfinite(t0) or not np.isfinite(t1) or t0 >= t1:
            raise ValueError("Choose finite endpoints with t0 < t1")
        if isinstance(nBlocks, (bool, np.bool_)) or not isinstance(nBlocks, (int, np.integer)) or nBlocks < 1:
            raise ValueError("nBlocks must be a positive integer")
        if uncertainty not in {"iid", "hac"}:
            raise ValueError("uncertainty must be 'iid' or 'hac'")
        
        t_all, x_all = self._array()
        valid = np.isfinite(t_all) & np.isfinite(x_all) & (t_all >= t0) & (t_all <= t1)
        
        if self.excluded_zero:
            valid &= x_all != 0
        indices = np.flatnonzero(valid)
        indices = indices[np.argsort(t_all[indices], kind="stable")]
        t, x = t_all[indices], x_all[indices]
        if len(t) < 3 or len(np.unique(t)) < 3:
            raise ValueError(f"Range {t0:g}–{t1:g} s needs >=3 valid points at distinct times")
        sigma_x = None if self.position_unc is None else self._sigma(
            self.position_unc, len(t_all), indices, "position_unc", positive=True)
        sigma_t = self._sigma(self.time_unc, len(t_all), indices, "time_unc")
        '''
        uncertainty department
        '''
        if method == "auto":
            method = "eiv" if sigma_x is not None and np.any(sigma_t) else ("wls" if sigma_x is not None else "ols")
        if method not in {"ols", "wls", "eiv"}:
            raise ValueError("method must be 'auto', 'ols', 'wls', or 'eiv'")
        if method in {"wls", "eiv"} and sigma_x is None:
            raise ValueError(f"{method} requires a measured position_unc (one-sigma, mm)")
        beta, cov, residual, design, reference, scale = _linear_fit(t, x, sigma_x if method != "ols" else None)

        if method == "eiv" and np.any(sigma_t):
            z = design[:, 1]
            def objective(b):
                sigma = np.hypot(sigma_x, b[1] / scale * sigma_t)
                return (x - b[0] - b[1] * z) / sigma

            result = least_squares(objective, beta, jac="3-point", x_scale="jac",
                                   ftol=1e-12, xtol=1e-12, gtol=1e-12, max_nfev=2000)
            if (not result.success or not np.all(np.isfinite(result.x))
                    or not np.all(np.isfinite(result.fun)) or np.linalg.matrix_rank(result.jac) < 2):
                raise ValueError("Errors-in-variables fit did not converge to a finite, identifiable line")
            beta = result.x
            cov = np.linalg.inv(result.jac.T @ result.jac)
            residual = x - design @ beta
            jacobian, standardized = result.jac, result.fun
        else:
            effective = np.ones(len(t)) if method == "ols" else sigma_x
            jacobian = -design / effective[:, None]
            standardized = residual / effective
        velocity = float(beta[1] / scale)
        effective_sigma = None if method == "ols" else np.hypot(
            sigma_x, velocity * sigma_t if method == "eiv" else 0)
        chi2 = None if effective_sigma is None else float(np.sum((residual / effective_sigma) ** 2) / (len(t) - 2))
        if uncertainty == "hac":
            if hac_lags is None:
                hac_lags = min(len(t) - 1, int(np.floor(4 * (len(t) / 100) ** (2 / 9))))
            if isinstance(hac_lags, (bool, np.bool_)) or not isinstance(hac_lags, (int, np.integer)) or not 0 <= hac_lags < len(t):
                raise ValueError("hac_lags must be an integer from 0 to n_points-1")
            cov_hac = _hac_cov(jacobian, standardized, hac_lags)
            # Do not report less uncertainty than the calibrated/IID estimate.
            cov[1, 1] = max(cov[1, 1], cov_hac[1, 1])
        u_fit = float(np.sqrt(max(0.0, cov[1, 1])) / scale)
        wall_fields = self._wall_fields(velocity, u_fit, W_mode)
        sse = float(residual @ residual)
        sst = float(np.sum((x - x.mean()) ** 2))
        r2 = float(1 - sse / sst) if sst > 0 else None
        lag1 = None
        if len(t) >= 4 and np.std(residual[:-1]) > 1e-12 and np.std(residual[1:]) > 1e-12:
            lag1 = float(np.corrcoef(residual[:-1], residual[1:])[0, 1])
        acceleration, u_acceleration = None, None
        if len(t) >= 5:
            qdesign = np.column_stack((design, design[:, 1] ** 2))
            weight = np.ones(len(t)) if effective_sigma is None else 1 / effective_sigma
            qm = qdesign * weight[:, None]
            qb, _, rank, _ = np.linalg.lstsq(qm, x * weight, rcond=None)
            if rank == 3:
                qc = np.linalg.inv(qm.T @ qm)
                if effective_sigma is None:
                    qr = x - qdesign @ qb
                    qc *= float(qr @ qr / (len(t) - 3))
                acceleration = float(2 * qb[2] / scale**2)
                u_acceleration = float(2 * np.sqrt(max(0.0, qc[2, 2])) / scale**2)
        block_velocity = []
        for block in np.array_split(np.arange(len(t)), min(nBlocks, len(t))):
            if len(block) >= 3 and len(np.unique(t[block])) >= 3:
                bb, _, _, _, _, bs = _linear_fit(t[block], x[block])
                block_velocity.append(float(bb[1] / bs))
        
        return RangeFit(
            selection_id="preview",
            choosen_t0=float(t0), choosen_t1=float(t1),
            measured_t0_pt=float(t[0]), measured_t1_point=float(t[-1]),
            n_points=len(t), velocity=velocity, u_fit=u_fit,
            wall_mode="Auto" if W_mode.lower() == "auto" else W_mode.lower(),
            **wall_fields,
            interception=float(beta[0] - velocity * reference),
            t_reference=reference, x_reference=float(beta[0]),
            method=method, uncertainty_model=uncertainty,
            residual_rms=float(np.sqrt(sse / len(t))), r_squared=r2,
            reduced_chi_2=chi2, residual_lag1=lag1,
            acceleration=acceleration, u_acceleration=u_acceleration,
            block_velocity=tuple(block_velocity),
            point_indices=tuple(map(int, indices)), data_hash=self._fingerprint(),
            x_sigma=None if sigma_x is None else tuple(map(float, sigma_x)),
            t_sigma=tuple(map(float, sigma_t)),
            hac_lags=hac_lags if uncertainty == "hac" else None,
            timing_model=self.timing_model, excluded_zero=self.excluded_zero,
        )

    def _set_current(self, result):
        self.current_fit = result
        self.fit = (result.measured_t0_pt, result.measured_t1_point, result.velocity, result.interception)
        self.v_err = result.u_fit
        self.summary = None

    def fit_range(self, t0, t1, n_blocks=5, *, wall_corrected=False, **kwargs):
        """Preview: raw (v,u_fit) default; corrected (C*v,fit+wall uncertainty) opt-in.
        Full corrected uncertainty including calibration is on current_fit.
        Larry's comment: algo and camera provide data with no uncertainty.
        """
        result = self._calculate_fit(t0, t1, nBlocks=n_blocks, **kwargs)
        self._set_current(result)
        if wall_corrected:
            return result.velocity_wall_corrected, result.velocity_wall_cor_unc
        return result.velocity, result.u_fit

    def _save_fit(self, result, name=None):
        self._check_wall_inputs([result])
        if result.data_hash != self._fingerprint():
            raise ValueError("Tracking data changed after the preview; select the range again")
        for selection in self.selections:
            if selection.data_hash != result.data_hash:
                raise ValueError("Tracking data changed; clear previous selections before saving")
        if name is None:
            while any(s.selection_id == f"S{self._next_selection:03d}" for s in self.selections):
                self._next_selection += 1
            name = f"S{self._next_selection:03d}"
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Selection name must be a nonempty string")
        if any(s.selection_id == name for s in self.selections):
            raise ValueError(f"Selection {name!r} already exists")
        if any(s.point_indices == result.point_indices and s.method == result.method and
               s.uncertainty_model == result.uncertainty_model and
               s.wall_mode == result.wall_mode for s in self.selections):
            raise ValueError("This point range and fit method are already saved; choose another range")
        saved = replace(result, selection_id=name)
        self.selections.append(saved)
        self._next_selection += 1
        self._set_current(saved)
        return saved

    def save_selection(self, t0, t1, *, name=None, n_blocks=5, **kwargs):
        return self._save_fit(self._calculate_fit(t0, t1, nBlocks=n_blocks, **kwargs), name)

    def clear_selections(self):
        self.selections.clear()
        self.fit = self.v_err = self.current_fit = self.summary = None
        self._next_selection = 1

    def remove_selection(self, selection_id):
        if not any(s.selection_id == selection_id for s in self.selections):
            raise KeyError(selection_id)
        remaining = []
        for selection in self.selections:
            if selection.selection_id != selection_id:
                remaining.append(selection)
        self.selections[:] = remaining
        self.fit = self.v_err = self.current_fit = self.summary = None

    def selection_table(self):
        rows = []
        for s in self.selections:
            rows.append({
                "id": s.selection_id,
                "choosen_t0_s": s.choosen_t0, "choosen_t1_s": s.choosen_t1,
                "measured_t0_pt_s": s.measured_t0_pt,
                "measured_t1_point_s": s.measured_t1_point,
                "n": s.n_points, "v_mm_s": s.velocity, "u_fit_mm_s": s.u_fit,
                "wall_mode": s.wall_mode, "wall_size_mm": s.wall_size_mm,
                "wall_coef": s.wall_coef, "wall_cor_unc": s.wall_cor_unc,
                "velocity_wall_corrected": s.velocity_wall_corrected,
                "velocity_wall_cor_unc": s.velocity_wall_cor_unc,
                "velocity_wall_cor_total_unc": s.velocity_wall_cor_total_unc,
                "u_calibration": s.u_calibration,
                "method": s.method, "uncertainty_model": s.uncertainty_model,
                "r_squared": s.r_squared, "reduced_chi_2": s.reduced_chi_2,
            })
        return rows

    def combine_selection(self, selection_ids=None):
        """Equal-weight average; not assuming same-run ranges are independent.
            u_fit = mean(u_i), a conservative correlation bound (rho_ij=+1).
            u_selection = sample SD of selected velocities, not SD/sqrt(M).
            u_total = quadrature of u_fit, u_selection, and calibration components.
        This total is a declared sensitivity budget, not a confidence interval.
        Corrected budget: C^2*u_total^2 + mean(v)^2*u_C^2. 

        C is shared,so its uncertainty is added once after averaging selected ranges.
        """
        if selection_ids is None:
            ids = []
            for selection in self.selections:
                ids.append(selection.selection_id)
        else:
            ids = list(selection_ids)
        if not ids or len(set(ids)) != len(ids):
            raise ValueError("Choose at least one distinct saved selection ID")
        lookup = {}
        for selection in self.selections:
            lookup[selection.selection_id] = selection
        unknown = set(ids) - lookup.keys()
        if unknown:
            raise KeyError(f"Unknown selections: {sorted(unknown)}")
        chosen = []
        for sid in ids:
            chosen.append(lookup[sid])
        fingerprint = self._fingerprint()
        if any(s.data_hash != fingerprint for s in chosen):
            raise ValueError("Tracking data changed since fitting; clear and refit selections")
        self._check_wall_inputs(chosen)
        if len({(s.wall_mode, s.wall_input_hash) for s in chosen}) != 1:
            raise ValueError("Combine selections with one wall mode; min/max are separate sensitivity scenarios")
        coefficient, uc = chosen[0].wall_coef, chosen[0].wall_cor_unc
        velocities = np.array([s.velocity for s in chosen])
        velocity = float(np.mean(velocities))
        u_fit = float(np.mean([s.u_fit for s in chosen]))
        u_selection = float(np.std(velocities, ddof=1)) if len(chosen) > 1 else 0.0
        u_cal = abs(velocity) * np.hypot(
            _nonnegative(self.scale_rel_unc, "scale_rel_unc"),
            _nonnegative(self.fps_unc, "fps_unc"))
        overlap = False
        for i, a in enumerate(chosen):
            for b in chosen[i + 1:]:
                if set(a.point_indices).intersection(b.point_indices):
                    overlap = True
                    break
            if overlap:
                break
        self.summary = Velocity_Summary(
            selection_ids=tuple(ids), velocity=velocity, u_fit=u_fit,
            u_selection=u_selection, u_calibration=float(u_cal),
            u_total=float(np.sqrt(u_fit**2 + u_selection**2 + u_cal**2)),
            overlap_domain=overlap,
            wall_mode=chosen[0].wall_mode, wall_coef=coefficient, wall_cor_unc=uc,
            velocity_wall_corrected=coefficient*velocity,
            u_fit_wall_corrected=coefficient*u_fit,
            u_selection_wall_corrected=coefficient*u_selection,
            u_calibration_wall_corrected=float(coefficient*u_cal),
            u_wall=abs(velocity)*uc,
            velocity_wall_cor_unc=float(np.sqrt(
                coefficient**2*(u_fit**2+u_selection**2+u_cal**2) + velocity**2*uc**2)),
        )
        self.v_err = self.summary.u_total
        # There is no single interception/range for an averaged velocity.
        self.fit = None
        self.current_fit = None
        return self.summary

    def displacement_uncertainty(self, time_interval, time_unc, *, selection_id=None,
                                 velo_uncertain="total", cov_t_v=0.0):
        """
        Propagate a fitted velocity and an elapsed time through d = v*t.
        u_d^2 = t^2*u_v^2 + v^2*u_t^2 + 2*t*v*Cov(t,v).
        """
        tau = _nonnegative(time_interval, "time_interval")
        ut = _nonnegative(time_unc, "time_unc")
        if velo_uncertain not in {"total", "fit"}:
            raise ValueError("velo_uncertain must be 'total' or 'fit'")
        if not np.isscalar(cov_t_v) or not np.isfinite(cov_t_v):
            raise ValueError("cov_t_v must be a finite scalar")
        covariance = float(cov_t_v)
        if selection_id is None and self.summary is not None:
            source_ids = self.summary.selection_ids
            chosen = []
            for sid in source_ids:
                found_selection = None
                for selection in self.selections:
                    if selection.selection_id == sid:
                        found_selection = selection
                        break
                chosen.append(found_selection)
            if any(s is None for s in chosen):
                raise ValueError("Summary selections changed; recombine before propagating")
            velocity = self.summary.velocity
            uv = self.summary.u_total if velo_uncertain == "total" else self.summary.u_fit
            basis = f"summary_{velo_uncertain}"
        else:
            source = self.current_fit
            if selection_id is not None:
                source = None
                for selection in self.selections:
                    if selection.selection_id == selection_id:
                        source = selection
                        break
            if source is None:
                raise ValueError("Fit a range, combine selections, or supply a valid saved selection_id first")
            chosen = [source]
            source_ids = (source.selection_id,)
            velocity, uv = source.velocity, source.u_fit
            basis = "range_fit"
            if velo_uncertain == "total":
                ucal = abs(velocity) * np.hypot(
                    _nonnegative(self.scale_rel_unc, "scale_rel_unc"),
                    _nonnegative(self.fps_unc, "fps_unc"))
                uv = float(np.hypot(uv, ucal))
                basis = "range_fit_and_calibration"

        if any(s.data_hash != self._fingerprint() for s in chosen):
            raise ValueError("Tracking data changed since fitting; refit before propagating")
        self._check_wall_inputs(chosen)
        # A covariance must satisfy the Cauchy-Schwarz inequality.
        bound = uv * ut
        if abs(covariance) > bound * (1 + 1e-12):
            raise ValueError("Covariance must satisfy abs(Cov(t,v)) <= time_unc * v_unc")
        from_time = abs(velocity) * ut
        from_velocity = tau * uv
        cross = 2 * tau * velocity * covariance
        variance = from_time**2 + from_velocity**2 + cross
        return Delta_x_Estimate(
            delta_t=tau, time_unc=ut, velocity=float(velocity), v_unc=float(uv),
            displacement=float(velocity * tau), u_from_t=float(from_time),
            u_from_v=float(from_velocity), cov_term_variance=float(cross),
            u_displacement=float(np.sqrt(max(0.0, variance))),
            source_selection_ids=tuple(source_ids), uncertainty_basis=basis,
        )

    def graph(self, ax=None, **kwargs):
        if ax is None:
            _, ax = plt.subplots()
        t, x = self._array()
        valid = np.isfinite(t) & np.isfinite(x)
        if self.excluded_zero:
            valid &= x != 0
        # NaN breaks the line.
        ax.plot(t, np.where(valid, x, np.nan),
                label=f"run {self.run_id}, group {self.group_id}", **kwargs)
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Position (mm)")
        return ax

    @classmethod
    def plot_size_group(cls, beads, size_group, ax=None, *, liquid_type=None):
        if ax is None:
            _, ax = plt.subplots()
        target = []
        for bead in beads:
            if bead.group_id == size_group and (liquid_type is None or bead.Liquid_type == liquid_type):
                target.append(bead)
        for bead in target:
            bead.graph(ax=ax)
        ax.set_title(f"Group {size_group}: {len(target)} beads")
        if target:
            ax.legend()
        return ax

    def plot_selection(self, selection_id=None, *, residual=True):
        result = self.current_fit
        if selection_id is not None:
            result = None
            for selection in self.selections:
                if selection.selection_id == selection_id:
                    result = selection
                    break
        if result is None:
            raise ValueError("Choose a saved selection ID or fit a current range first")
        if result.data_hash != self._fingerprint():
            raise ValueError("Tracking data changed since fitting")
        t_all, x_all = self._array()
        indices = np.array(result.point_indices)
        t, x = t_all[indices], x_all[indices]
        fig, axes = plt.subplots(2 if residual else 1, 1, squeeze=False, sharex=True,
                                 figsize=(8, 6 if residual else 4), layout="constrained")
        ax = axes[0, 0]
        self.graph(ax, color="0.75")
        ax.plot(t, x, ".", label=result.selection_id)
        ax.plot(t, result.prediction(t), "k--", label="Linear fit")
        self._check_wall_inputs([result])
        ax.set_title(f"{self.Liquid_type}, run {self.run_id}: measured v = {result.velocity:.4g} ± {result.u_fit:.2g} mm/s\n"
                     f"C*v = {result.velocity_wall_corrected:.4g} ± {result.velocity_wall_cor_total_unc:.2g} mm/s (fit+wall+cal)")
        ax.legend()
        if residual:
            axes[1, 0].axhline(0, color="0.5", lw=1)
            axes[1, 0].plot(t, x - result.prediction(t), ".")
            axes[1, 0].set_ylabel("Residual (mm)")
            axes[1, 0].set_xlabel("Time (s)")
        return fig, axes[:, 0]

    def range_select(self, *, method="auto", uncertainty="iid", n_blocks=5,
                     hac_lags=None, W_mode="Auto", wall_corrected=False):
        previous = (list(self.selections), self._next_selection, self.current_fit,
                    self.fit, self.v_err, self.summary)
        ax = self.graph(marker=".", markersize=2, lw=0.7)
        fig = ax.figure
        fig.set_size_inches(12, 7)
        fig.subplots_adjust(left=0.08, right=0.68, bottom=0.30, top=0.87)
        fig.canvas.manager.set_window_title(f"{self.Liquid_type} | run {self.run_id} | group {self.group_id}")
        chooser_ax = fig.add_axes([0.73, 0.30, 0.25, 0.55])
        preview_line, = ax.plot([], [], "k--", lw=1.4)
        residual_ax = fig.add_axes([0.08, 0.14, 0.60, 0.12], sharex=ax)
        residual_line, = residual_ax.plot([], [], ".", markersize=3)
        residual_ax.axhline(0, color="0.6", lw=0.8)
        residual_ax.set_ylabel("Residual\n(mm)")
        residual_ax.set_xlabel("Time (s)")
        preview = None
        accepted = False
        checked = set(self.summary.selection_ids) if self.summary else {s.selection_id for s in self.selections}
        check_widget = None
        saved_lines = []
        fig.text(0.08, 0.94, "Drag a range; inspect residuals; Save. Check ranges on the right; Finish averages them.", fontsize=10)

        def message(text):
            ax.set_title(text, fontsize=10)
            fig.canvas.draw_idle()

        def redraw_chooser():
            nonlocal check_widget
            if check_widget is not None:
                check_widget.disconnect_events()
            chooser_ax.clear()
            chooser_ax.set_axis_on()
            for line in saved_lines:
                line.remove()
            saved_lines.clear()
            if self.selections:
                labels, states, mapping = [], [], {}
                for selection in self.selections:
                    label = (f"{selection.selection_id} [{selection.wall_mode}]: v={selection.velocity:.4g}\n"
                             f"C*v={selection.velocity_wall_corrected:.4g} mm/s; "
                             f"{selection.measured_t0_pt:.3g}–{selection.measured_t1_point:.3g} s")
                    labels.append(label)
                    states.append(selection.selection_id in checked)
                    mapping[label] = selection.selection_id
                check_widget = CheckButtons(chooser_ax, labels, states)
                def toggle(label):
                    sid = mapping[label]
                    if sid in checked:
                        checked.discard(sid)
                    else:
                        checked.add(sid)
                    fig.canvas.draw_idle()
                check_widget.on_clicked(toggle)
                for s in self.selections:
                    ts = np.array([s.measured_t0_pt, s.measured_t1_point])
                    line, = ax.plot(ts, s.prediction(ts), lw=1, alpha=0.5)
                    saved_lines.append(line)
            else:
                check_widget = None
                chooser_ax.text(0.05, 0.85, "No saved ranges yet", transform=chooser_ax.transAxes)
                chooser_ax.set_axis_off()
            fig._bead_check_widget = check_widget
            fig.canvas.draw_idle()

        def on_selection(t0, t1):
            nonlocal preview
            try:
                preview = self._calculate_fit(t0, t1, method=method, uncertainty=uncertainty,
                                             nBlocks=n_blocks, hac_lags=hac_lags, W_mode=W_mode)
            except ValueError as exc:
                preview = None
                preview_line.set_data([], [])
                residual_line.set_data([], [])
                message(str(exc))
                return
            ts = np.array([preview.measured_t0_pt, preview.measured_t1_point])
            preview_line.set_data(ts, preview.prediction(ts))
            t_all, x_all = self._array()
            idx = np.array(preview.point_indices)
            residual_line.set_data(t_all[idx], x_all[idx] - preview.prediction(t_all[idx]))
            residual_ax.relim()
            residual_ax.autoscale_view(scalex=False)
            message(f"Preview {preview.measured_t0_pt:.3g}–{preview.measured_t1_point:.3g}s: v={preview.velocity:.4g} ± {preview.u_fit:.2g} mm/s\n"
                    f"C*v={preview.velocity_wall_corrected:.4g} ± {preview.velocity_wall_cor_total_unc:.2g} mm/s (fit+wall+cal); "
                    f"{preview.n_points} points; {preview.method}")

        def save(event=None):
            if preview is None:
                message("Drag a valid range first")
                return
            try:
                saved = self._save_fit(preview)
            except ValueError as exc:
                message(str(exc))
                return
            checked.add(saved.selection_id)
            redraw_chooser()
            message(f"Saved {saved.selection_id}. Select another range, or check ranges and Finish.")

        def undo(event=None):
            if self.selections:
                sid = self.selections[-1].selection_id
                self.remove_selection(sid)
                checked.discard(sid)
                redraw_chooser()
                message(f"Removed {sid}")

        def finish(event=None):
            nonlocal accepted
            try:
                chosen_ids = []
                for selection in self.selections:
                    if selection.selection_id in checked:
                        chosen_ids.append(selection.selection_id)
                self.combine_selection(chosen_ids)
            except (ValueError, KeyError) as exc:
                message(str(exc))
                return
            accepted = True
            plt.close(fig)

        selector = SpanSelector(ax, on_selection, "horizontal", useblit=True,
                                props={"alpha": 0.2, "facecolor": "tab:red"}, interactive=True)
        buttons = []
        for left, label, callback in [(0.08, "Save range", save), (0.25, "Undo last", undo),
                                      (0.42, "Finish", finish), (0.59, "Cancel", lambda e: plt.close(fig))]:
            button = Button(fig.add_axes([left, 0.025, 0.14, 0.05]), label)
            button.on_clicked(callback)
            buttons.append(button)
        def keypress(event):
            if event.key == "s":
                save()
            elif event.key == "enter":
                finish()
            elif event.key == "escape":
                plt.close(fig)
        fig.canvas.mpl_connect("key_press_event", keypress)
        redraw_chooser()
        message("Drag across the position plot to preview a linear fit")
        # Keep widgets alive for the entire blocking desktop interaction.
        fig._bead_widgets = (selector, buttons)
        plt.show(block=True)
        if not accepted:
            old, self._next_selection, self.current_fit, self.fit, self.v_err, self.summary = previous
            self.selections[:] = old
            return None
        if wall_corrected:
            return self.summary.velocity_wall_corrected, self.summary.velocity_wall_cor_unc
        return self.summary.velocity, self.summary.u_total

    def rangeselect(self, **kwargs):
        return self.range_select(**kwargs)

    def save_session(self, path):
        """Save fit history and chosen IDs to JSON; tracked data stay in CSV."""
        settings = {
            "time_unc": np.asarray(self.time_unc, dtype=float).tolist(),
            "position_unc": (None if self.position_unc is None else
                             np.asarray(self.position_unc, dtype=float).tolist()),
            "timing_model": self.timing_model, "excluded_zero": self.excluded_zero,
            "fps": self.fps,
        }
        self._check_wall_inputs(self.selections)
        settings.update({"diameter": self.diameter, "diameter_unc": self.diameter_unc,
                         "wall_size_mm": self.wall_size_mm, "wall_size_unc": self.wall_size_unc,
                         "wall_model_rel_unc": self.wall_model_rel_unc,
                         "cov_diameter_wall_size": self.cov_diameter_wall_size})
        payload = {"schema_ver": 2, "key": list(self.key), "data_hash": self._fingerprint(),
                   "selections": [asdict(s) for s in self.selections],
                   "chosen_ids": list(self.summary.selection_ids) if self.summary else None,
                   "scale_rel_unc": self.scale_rel_unc, "fps_unc": self.fps_unc,
                   "fit_settings": settings}
        if any(s.data_hash != payload["data_hash"] for s in self.selections):
            raise ValueError("Tracking data changed; clear and refit before saving")
        target = Path(path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8")
        return target

    def load_selections(self, path):
        """Restore a saved session only when bead identity and data match exactly."""
        payload = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Session must contain a JSON object")
        if payload.get("schema_ver") not in {1, 2} or payload.get("key") != list(self.key):
            raise ValueError("Session schema or bead identity does not match")
        if payload.get("data_hash") != self._fingerprint():
            raise ValueError("Session tracking data do not match; load the original CSV data first")
        settings = payload.get("fit_settings", {})
        wall_keys = ("diameter", "diameter_unc", "wall_size_mm", "wall_size_unc",
                     "wall_model_rel_unc", "cov_diameter_wall_size")
        wall_settings = {key: settings.get(key, getattr(self, key)) for key in wall_keys}
        wall_candidate = replace(self, **wall_settings,
                                 scale_rel_unc=payload["scale_rel_unc"], fps_unc=payload["fps_unc"])
        restored = []
        for row in payload["selections"]:
            row = dict(row)
            for key in ("point_indices", "block_velocity", "t_sigma"):
                row[key] = tuple(row[key])
            if row.get("x_sigma") is not None:
                row["x_sigma"] = tuple(row["x_sigma"])
            mode = row.get("wall_mode", "Auto")
            expected = wall_candidate._wall_fields(row["velocity"], row["u_fit"], mode)
            if payload["schema_ver"] == 2:
                for key, value in expected.items():
                    if isinstance(value, str):
                        matches = row.get(key) == value
                    else:
                        matches = key in row and np.isclose(row[key], value, rtol=1e-10, atol=1e-12)
                    if not matches:
                        raise ValueError(f"Saved wall field {key} is inconsistent with geometry/calibration")
            row.update(expected)
            row["wall_mode"] = "Auto" if mode.lower() == "auto" else mode.lower()
            result = RangeFit(**row)
            if result.data_hash != payload["data_hash"]:
                raise ValueError("Saved fit fingerprint does not match the session")
            t_all, x_all = self._array()
            indices = np.asarray(result.point_indices)
            if (indices.ndim != 1 or not np.issubdtype(indices.dtype, np.integer)
                    or len(indices) < 3 or len(indices) != result.n_points
                    or len(set(result.point_indices)) != len(indices)
                    or np.any(indices < 0) or np.any(indices >= len(t_all))):
                raise ValueError("Saved fit contains invalid point indices")
            t, x = t_all[indices], x_all[indices]
            if (np.any(~np.isfinite(t)) or np.any(~np.isfinite(x))
                    or np.any(np.diff(t) < 0) or len(np.unique(t)) < 3
                    or not np.isfinite(result.choosen_t0) or not np.isfinite(result.choosen_t1)
                    or result.choosen_t0 >= result.choosen_t1
                    or np.any(t < result.choosen_t0) or np.any(t > result.choosen_t1)
                    or result.measured_t0_pt != float(t[0]) or result.measured_t1_point != float(t[-1])):
                raise ValueError("Saved fit contains inconsistent range bounds")
            if (not isinstance(result.selection_id, str) or not result.selection_id.strip()
                    or result.method not in {"ols", "wls", "eiv"}
                    or result.uncertainty_model not in {"iid", "hac"}):
                raise ValueError("Saved fit contains invalid identity or method")
            _nonnegative(result.u_fit, "saved u_fit")
            if not np.all(np.isfinite([result.velocity, result.interception,
                                       result.t_reference, result.x_reference])):
                raise ValueError("Saved fit coefficients must be finite")
            if result.excluded_zero and np.any(x == 0):
                raise ValueError("Saved fit includes zero positions despite excluded_zero=True")
            if len(result.t_sigma) != len(indices):
                raise ValueError("Saved t_sigma must match the selected points")
            self._sigma(result.t_sigma, len(indices), np.arange(len(indices)), "saved t_sigma")
            if result.x_sigma is not None:
                if len(result.x_sigma) != len(indices):
                    raise ValueError("Saved x_sigma must match the selected points")
                self._sigma(result.x_sigma, len(indices), np.arange(len(indices)), "saved x_sigma", positive=True)
            if result.method != "ols" and result.x_sigma is None:
                raise ValueError("Saved WLS/EIV fit requires x_sigma")
            restored.append(result)
        ids = [s.selection_id for s in restored]
        chosen = payload.get("chosen_ids")
        if len(set(ids)) != len(ids) or (chosen is not None and (not chosen or len(set(chosen)) != len(chosen) or set(chosen) - set(ids))):
            raise ValueError("Session contains invalid selection IDs")
        scale_unc = _nonnegative(payload["scale_rel_unc"], "scale_rel_unc")
        fps_unc = _nonnegative(payload["fps_unc"], "fps_unc")
        # Validation department:
        # combine on a temporary bead. A bad session leaves this
        # bead's existing data, selections, and summary intact.
        settings = payload.get("fit_settings", {})
        candidate = replace(
            self, scale_rel_unc=scale_unc, fps_unc=fps_unc, **wall_settings,
            time_unc=settings.get("time_unc", self.time_unc),
            position_unc=settings.get("position_unc", self.position_unc),
            timing_model=settings.get("timing_model", self.timing_model),
            excluded_zero=settings.get("excluded_zero", self.excluded_zero),
            fps=settings.get("fps", self.fps),
        )
        t_all, _ = self._array()
        all_indices = np.arange(len(t_all))
        candidate._sigma(candidate.time_unc, len(t_all), all_indices, "time_unc")
        if candidate.position_unc is not None:
            candidate._sigma(candidate.position_unc, len(t_all), all_indices, "position_unc", positive=True)
        candidate.selections[:] = restored
        candidate._next_selection = len(restored) + 1
        if chosen:
            candidate.combine_selection(chosen)
        self.selections[:] = candidate.selections
        self._next_selection = candidate._next_selection
        self.summary, self.current_fit = candidate.summary, candidate.current_fit
        self.fit, self.v_err = candidate.fit, candidate.v_err
        self.scale_rel_unc, self.fps_unc = candidate.scale_rel_unc, candidate.fps_unc
        for key in wall_keys:
            setattr(self, key, getattr(candidate, key))
        self.time_unc, self.position_unc = candidate.time_unc, candidate.position_unc
        self.timing_model, self.excluded_zero, self.fps = candidate.timing_model, candidate.excluded_zero, candidate.fps
        return self

    def summary_row(self):
        if self.summary is None:
            raise ValueError("Combine saved selections first")
        chosen = [s for s in self.selections if s.selection_id in self.summary.selection_ids]
        if len(chosen) != len(self.summary.selection_ids):
            raise ValueError("Summary selections changed; recombine")
        self._check_wall_inputs(chosen)
        if any(s.data_hash != self._fingerprint() for s in chosen):
            raise ValueError("Tracking data changed; refit selections")
        row = asdict(self.summary)
        row.update(Liquid_type=self.Liquid_type, run_id=self.run_id,
                   group_id=self.group_id, diameter=self.diameter,
                   diameter_unc=self.diameter_unc, wall_size_mm=chosen[0].wall_size_mm)
        row["selection_ids"] = "; ".join(row["selection_ids"])
        row["test"] = "; ".join(row["test"])
        return row

    @classmethod
    def export_summaries(cls, beads, path):
        """Export run summaries with columns used by Unified_Bead loading."""
        rows = [bead.summary_row() for bead in beads]
        if not rows:
            raise ValueError("Supply at least one bead with a summary")
        target = Path(path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        return target

    def export_selections(self, path):
        rows = self.selection_table()
        if not rows:
            raise ValueError("No saved selections to export")
        target = Path(path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        return target

@dataclass
class Unified_Bead:
    Liquid_type: str
    Size_grp: int
    Run_ids: list[int]
    Dia_lists: list[float]
    Dia_uncs: float
    V_lists: list[float]
    V_tot_uncs: list[float]
    Velocity: float
    Velocity_tot_unc: float
    Diameter: float
    Diameter_unc:float
    wall_corrected: bool = False
    u_shared_wall: float = 0.0
    u_shared_calibration: float = 0.0

    def __str__(self):
        return (f"This bead is for liquid {self.Liquid_type}, the diameter is: {self.Diameter} ± {self.Diameter_unc}. "
            f"The terminal velocity is: {self.Velocity} ± {self.Velocity_tot_unc}. This data gathered from trail: {self.Run_ids}")

    @classmethod
    def _unify_velocity(cls, velo_list, v_unc_list):
        """Independent-run random-effects budget; returns STANDARD uncertainty.

        Supply independent components only. Shared wall/calibration components
        must be added after this calculation. No drop-to-drop covariance is
        inferred here (e.g. residual flow in glycol needs separate assessment).
        """
        v = np.asarray(velo_list, dtype=float)
        u = np.asarray(v_unc_list, dtype=float)
        if (v.ndim != 1 or u.shape != v.shape or len(v) == 0
                or np.any(~np.isfinite(v)) or np.any(~np.isfinite(u)) or np.any(u < 0)):
            raise ValueError("Supply nonempty paired finite velocities and nonnegative uncertainties")
        n = len(v)
        sample_variance = float(np.var(v, ddof=1)) if n > 1 else 0.0
        tau2 = max(0.0, sample_variance - float(np.mean(u**2))) if n > 1 else 0.0
        variance = float(np.sum(u**2)/n**2 + tau2/n)
        return float(np.mean(v)), float(np.sqrt(variance))

    @classmethod
    def _unify_diameter(cls, d_lists, diameter_uncs=0.01):
        """Mean diameter with independent measurement uncertainties, in mm.

        Does not infer shared caliper error or physical bead-size dispersion.
        """
        d = np.asarray(d_lists, dtype=float)
        u = np.asarray(diameter_uncs, dtype=float)
        if u.ndim == 0:
            u = np.full(d.shape, float(u))
        if (d.ndim != 1 or len(d) == 0 or u.shape != d.shape
                or np.any(~np.isfinite(d)) or np.any(d <= 0)
                or np.any(~np.isfinite(u)) or np.any(u < 0)):
            raise ValueError("Supply positive finite diameters and paired nonnegative uncertainties")
        return float(np.mean(d)), float(np.sqrt(np.sum(u**2))/len(d))

    @staticmethod
    def _resolve_file(filename, data_dir=None):
        if data_dir is not None:
            path = Path(data_dir).expanduser() / filename
            if not path.is_file():
                raise FileNotFoundError(path)
            return path
        # Explicit data_dir is recommended; these preserve both old layouts.
        roots = (Path.cwd(), Path(__file__).resolve().parent, Path(__file__).resolve().parent.parent)
        for root in roots:
            path = root / filename
            if path.is_file():
                return path
        raise FileNotFoundError(f"Cannot find {filename}; supply data_dir explicitly")

    @classmethod
    def _from_summary_rows(cls, rows, *, wall_corrected=False):
        if not rows:
            raise ValueError("Supply at least one bead")
        liquids = {str(row["Liquid_type"]) for row in rows}
        groups = {int(row["group_id"]) for row in rows}
        if len(liquids) != 1 or len(groups) != 1:
            raise ValueError("Combine only one liquid and size group")
        run_ids = [int(row["run_id"]) for row in rows]
        if len(set(run_ids)) != len(run_ids):
            raise ValueError("Duplicate run IDs would count one drop more than once")
        if wall_corrected:
            required = ("velocity_wall_corrected", "velocity_wall_cor_unc",
                        "u_fit_wall_corrected", "u_selection_wall_corrected",
                        "u_calibration_wall_corrected", "u_wall", "wall_mode")
            if any(any(key not in row or row[key] == "" for key in required) for row in rows):
                raise ValueError("Corrected grouping needs full wall budget columns; use Bead.export_summaries")
            if len({row["wall_mode"] for row in rows}) != 1:
                raise ValueError("Use one wall mode per group; min/max are separate scenarios")
            v_key, total_key = "velocity_wall_corrected", "velocity_wall_cor_unc"
            wall = np.array([float(row["u_wall"]) for row in rows])
            cal = np.array([float(row["u_calibration_wall_corrected"]) for row in rows])
            fit_unc = np.array([float(row["u_fit_wall_corrected"]) for row in rows])
            selection_unc = np.array([float(row["u_selection_wall_corrected"]) for row in rows])
            if (np.any(~np.isfinite(fit_unc)) or np.any(fit_unc < 0)
                    or np.any(~np.isfinite(selection_unc)) or np.any(selection_unc < 0)):
                raise ValueError("Corrected fit and selection uncertainties must be finite and nonnegative")
            independent = np.hypot(fit_unc, selection_unc)
        else:
            v_key, total_key = "velocity", "u_total"
            wall = np.zeros(len(rows))
            cal = np.array([float(row.get("u_calibration", 0.0)) for row in rows])
            independent = None
        velocities = np.array([float(row[v_key]) for row in rows])
        totals = np.array([float(row[total_key]) for row in rows])
        for name, values in (("total", totals), ("wall", wall), ("calibration", cal)):
            if np.any(~np.isfinite(values)) or np.any(values < 0):
                raise ValueError(f"{name} uncertainties must be finite and nonnegative")
        if independent is None:
            # Older raw CSV files lack split budgets. Their totals can only be
            # treated as independent inputs, with this limitation recorded.
            variance = totals**2-cal**2
            if np.any(variance < -1e-10*np.maximum(totals**2, 1.0)):
                raise ValueError("Calibration component exceeds the supplied total uncertainty")
            independent = np.sqrt(np.maximum(0.0, variance))
        elif not np.allclose(totals**2, independent**2+wall**2+cal**2, rtol=1e-8, atol=1e-12):
            raise ValueError("Corrected total uncertainty does not match its split budget")
        velocity, u_ind = cls._unify_velocity(velocities, independent)
        # Conservative positive-correlation bounds across runs: shared wall
        # and calibration terms do not shrink as independent repeats.
        u_wall, u_cal = float(np.mean(wall)), float(np.mean(cal))
        diameter_list = [float(row["diameter"]) for row in rows]
        diameter_uncs = [float(row.get("diameter_unc", 0.01)) for row in rows]
        diameter, ud = cls._unify_diameter(diameter_list, diameter_uncs)
        return cls(
            Liquid_type=next(iter(liquids)), Size_grp=next(iter(groups)), Run_ids=run_ids,
            Dia_lists=diameter_list, Dia_uncs=float(max(diameter_uncs)),
            V_lists=velocities.tolist(), V_tot_uncs=totals.tolist(),
            Velocity=velocity, Velocity_tot_unc=float(np.sqrt(u_ind**2+u_wall**2+u_cal**2)),
            Diameter=diameter, Diameter_unc=ud, wall_corrected=wall_corrected,
            u_shared_wall=u_wall, u_shared_calibration=u_cal,
        )

    @classmethod
    def Unify_beads_by_bead(cls, beads, *, wall_corrected=False):
        """Group completed Bead summaries; opt into free-fluid velocities explicitly."""
        return cls._from_summary_rows([bead.summary_row() for bead in beads],
                                      wall_corrected=wall_corrected)

    @classmethod
    def Unify_bead_by_csv(cls, dir="Bead_data_analysis/Data_Analysis",
                         f="terminal_velocity.csv", *, wall_corrected=False):
        path = cls._resolve_file(f, dir)
        groups = defaultdict(list)
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                groups[(row["Liquid_type"], int(row["group_id"]))].append(row)
        if not groups:
            raise ValueError("CSV has no bead summaries")
        return [cls._from_summary_rows(rows, wall_corrected=wall_corrected)
                for rows in groups.values()]
