"""
Web UI for cutting a 3D model: split in half or scale to build volume and cut into N×N×N grid.
"""

import io
import shutil
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path

# Require rtree early (used by shapely/trimesh for mesh cutting)
try:
    import rtree  # noqa: F401
except ImportError:
    print(
        "Missing dependency: rtree. Install with:\n  pip install rtree\n"
        "On macOS you may also need:\n  brew install spatialindex",
        file=sys.stderr,
    )
    sys.exit(1)

from flask import Flask, jsonify, render_template, request, send_file

from mesh_cutter import (
    PEG_SIZES, add_peg_holes_to_grid, create_peg_mesh,
    cut_grid_by_cubes, cut_grid_by_scale, load_mesh,
)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100 MB

# Directory for temporary cut results (job_id -> dir path)
JOBS_DIR = Path(tempfile.gettempdir()) / "3dmesh_cut_jobs"
JOBS_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {
    "stl", "obj", "3mf", "ply", "off", "glb", "gltf", "dae", "x3d", "xml",
}


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route("/")
def index():
    return render_template("index.html")


def _parse_float(s, default=None):
    if s is None or (isinstance(s, str) and not s.strip()):
        return default
    try:
        return float(s)
    except (TypeError, ValueError):
        return default


def _parse_int(s, default=None):
    if s is None or (isinstance(s, str) and not s.strip()):
        return default
    try:
        return int(s)
    except (TypeError, ValueError):
        return default


@app.route("/cut", methods=["POST"])
def cut():
    if "file" not in request.files:
        return jsonify({"error": "No file selected"}), 400
    f = request.files["file"]
    if not f or not f.filename:
        return jsonify({"error": "No file selected"}), 400
    if not allowed_file(f.filename):
        return jsonify({
            "error": "Unsupported format. Use STL, OBJ, 3MF, PLY, etc."
        }), 400

    # Optional: build volume (mm) and number of cubes per axis for grid split
    vol = _parse_float(request.form.get("vol"))
    cubes = _parse_int(request.form.get("cubes"))
    user_scale = _parse_int(request.form.get("user_scale"))
    vol_ok = vol is not None and vol > 0
    use_grid_cubes = vol_ok and cubes is not None and cubes >= 2
    use_grid_scale = vol_ok and user_scale is not None and user_scale >= 1
    use_grid = use_grid_cubes or use_grid_scale

    job_id = str(uuid.uuid4())
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    input_path = job_dir / (f.filename or "model.stl")
    try:
        f.save(str(input_path))
        mesh = load_mesh(input_path)

        if not use_grid:
            shutil.rmtree(job_dir, ignore_errors=True)
            return jsonify({"error": "Please enter a build volume and grid parameters."}), 400

        basename = Path(f.filename).stem
        if use_grid_scale:
            cells, scale, nx, ny, nz, scaled_mesh = cut_grid_by_scale(mesh, vol, user_scale)
        else:
            cells, scale, nx, ny, nz, scaled_mesh = cut_grid_by_cubes(mesh, vol, cubes)

        # Add peg holes to all internal cut faces
        cells, peg_counts = add_peg_holes_to_grid(cells, vol)

        # Export cut parts
        for i, j, k, cell_mesh in cells:
            cell_mesh.export(str(job_dir / f"{basename}_part_{i}_{j}_{k}.stl"))

        # Export full scaled model
        scaled_mesh.export(str(job_dir / f"{basename}_scaled.stl"))

        # Export one peg STL per size used
        peg_depth_map = {diam: depth for _, diam, depth, _ in PEG_SIZES}
        for diam in sorted(peg_counts.keys()):
            depth = peg_depth_map[diam]
            peg = create_peg_mesh(diam, depth)
            peg.export(str(job_dir / f"peg_{int(diam)}mm.stl"))

        # Build manifest
        lines = ["Pegs required:\n"]
        total_pegs = 0
        for diam in sorted(peg_counts.keys()):
            count = peg_counts[diam]
            total_pegs += count
            lines.append(f"  {int(diam)}mm diameter x{count}  (peg_{int(diam)}mm.stl)\n")
        lines.append(f"\nTotal: {total_pegs} pegs\n")
        manifest_text = "".join(lines)

        zip_path = job_dir / f"{basename}_grid.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in job_dir.glob("*.stl"):
                zf.write(p, p.name)
            zf.writestr("pegs_manifest.txt", manifest_text)

        return jsonify({
            "job_id": job_id,
            "mode": "grid",
            "grid_mode": "scale" if use_grid_scale else "cubes",
            "basename": basename,
            "vol": vol,
            "nx": nx,
            "ny": ny,
            "nz": nz,
            "num_parts": len(cells),
            "zip_filename": zip_path.name,
            "scale": scale,
            "peg_counts": {str(int(k)): v for k, v in peg_counts.items()},
        })
    except Exception as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        return jsonify({"error": str(e)}), 422


@app.route("/download/<job_id>")
def download(job_id):
    job_dir = JOBS_DIR / job_id
    if not job_dir.is_dir():
        return "Expired or invalid", 404
    zips = list(job_dir.glob("*.zip"))
    if not zips:
        return "Zip not found", 404
    path = zips[0]
    return send_file(
        path,
        as_attachment=True,
        download_name=path.name,
        mimetype="application/zip",
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=True)
