import csv, json
from dataclasses import asdict,fields
from pathlib import Path
from bead_class_final import Bead, RangeFit, Velocity_Summary, Unified_Bead

META = ["Liquid_type","run_id","group_id","diameter","pos_h","pos_w","fps","fps_unc","scale_rel_unc"]
SELECTION_FIELDS = META + ["including_in_average"]
for data_field in fields(RangeFit):
    SELECTION_FIELDS.append(data_field.name)
SUMMARY_FIELD = META + ["status","n_saved","n_chosen","speed"]
for data_field in fields(Velocity_Summary):
    SUMMARY_FIELD.append(data_field.name)
SUMMARY_FIELD += ["method","uncertainty_model","timing_model","uncertainty_rule"]

def write_to_csv(path, rows, fieldnames):
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w",encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f,fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            cell = dict(r)
            for n, v in cell.items():
                if isinstance(v,(tuple,list)):
                    cell[n] = json.dumps(v, allow_nan=False)
            writer.writerow(cell)
    temp.replace(path)

def bead_meta(bead):
    row = {}
    for n in META:
        row[n] = getattr(bead, n)
    return row

def sum_row(bead, status):
    row = bead_meta(bead)
    row.update(status=status, n_saved=len(bead.selections), n_chosen=0)
    if bead.summary is None:
        return row
    row.update(asdict(bead.summary))

    row["n_chosen"] = len(bead.summary.selection_ids)
    row["speed"] = bead.summary.speed
    meth, mod, t_mod = set(), set(), set()
    for s in bead.selections:
        if s.selection_id in bead.summary.selection_ids:
            meth.add(s.method)
            mod.add(s.uncertainty_model)
            t_mod.add(s.timing_model)
    row["method"] = "; ".join(sorted(meth))
    row["uncertainty_model"] = "; ".join(sorted(mod))
    row["timing_model"] = "; ".join(sorted(t_mod))
    return row

def save_bead_res(bead, output_dir):
    rows = []
    if not bead.selections:
        return rows
    name = f"{bead.Liquid_type}_run{bead.run_id:03d}"
    bead.save_session(output_dir/"session"/f"{name}.json")
    bead.export_selections(output_dir/"pre_bead"/f"{name}_selections.csv")
    chosen_id = set()
    if bead.summary is not None:
        chosen_id.update(bead.summary.selection_ids)
    for s in bead.selections:
        row = bead_meta(bead)
        row["including_in_average"] = s.selection_id in chosen_id
        row.update(asdict(s))
        rows.append(row)
    return rows

def bead_analysis(data_dir=None, out_dir=None, *, method="auto",uncertainty="iid",t_mod="one_frame",
         pos_unc=None, fps_unc=0.0, scale_rel_unc=0.0, n_blocks=5, hac_lags=None):
    if method not in {"auto","ols","eiv","wls"}:
        raise ValueError("unknown fitting method man")
    if uncertainty not in {"iid","eiv"}:
        raise ValueError("unknown uncertainty approach man")
    if method in {"wls","eiv"} and pos_unc is None:
        raise ValueError("man I need the direct position uncertainty to fit it this way")
    if data_dir is None:
        data_dir = Bead._resolve_file("general_bead_statistic.csv").parent
    data_dir = Path(data_dir).expanduser()
    if out_dir is None:
        out_dir = data_dir / "Bead_data_analysis"
    out_dir = Path(out_dir).expanduser()

    beads = Bead.create_beads(data_dir,timing_model=t_mod, position_unc=pos_unc,
                              fps_unc=fps_unc, scale_rel_unc=scale_rel_unc)
    
    liq = set()
    for b in beads:
        liq.add(b.Liquid_type)
    for l in sorted(liq):
        missing =Bead.load_all_pos(l, beads, data_dir)
        if missing:
            raise ValueError(f"The bead listed are missing data: {missing}")
        
    out_dir.mkdir(parents=True, exist_ok=True)
    selection_rows, summary_rows = [], []
    for b in beads:
        summary_rows.append(sum_row(b, "not_reviewed"))
    summary_path = out_dir/"Data_Analysis"/"terminal_velocity.csv"
    selection_path = out_dir/"Bead_data_analysis"/"all_selection.csv"
    write_to_csv(summary_path,summary_rows,SUMMARY_FIELD)
    write_to_csv(selection_path,selection_rows,SELECTION_FIELDS)

    print(f"[PROCESSING......] Reviewing{len(beads)} beads")

    for i, b in enumerate(beads):
        print(f"\nBead{i+1}/{len(beads)}:{b.Liquid_type} - run {b.run_id},", flush=True)
        interrupted = False
        try:
            res = b.range_select(method=method, uncertainty=uncertainty,n_blocks=n_blocks,hac_lags=hac_lags)
            status = "cancelled" if res is None else "accepted"
        except KeyboardInterrupt:
            status, interrupted = "interrupted", True
        selection_rows.extend(save_bead_res(b, out_dir))
        summary_rows[i] = sum_row(b,status)
        write_to_csv(selection_path, selection_rows,SELECTION_FIELDS)
        write_to_csv(summary_path,summary_rows,SUMMARY_FIELD)
        if b.summary is not None:
            print(f"[Result] v = {b.summary.velocity:.6g} \pm {b.summary.u_total:4g} mm/s")
        if interrupted:
            print("[------------------- Process Stopped -------------------]")
            break
    
    print("------------------------------------------SUMMARY------------------------------------------")
    print(f"Summary: {summary_path} \n All ranges: {selection_path}")
    return beads

if __name__ == "__main__":
    print("running")
    bead_analysis(uncertainty = "iid")
    Beads = Unified_Bead.Unify_bead_by_csv()
    #validation visual check
    for b in Beads:
        print(b)
        print("/n")
