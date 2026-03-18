"""
3D mesh cutting utilities: scale to build volume and split into an N×N×N grid
with alignment peg holes on all internal cut faces.
"""

import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import trimesh
from trimesh import intersections

# ---------------------------------------------------------------------------
# Peg size table: (min_face_shorter_side_mm, diameter_mm, depth_mm, max_pegs)
# Pegs are cylinders; one peg STL covers both mating holes (height = 2×depth).
# ---------------------------------------------------------------------------
PEG_SIZES: List[Tuple[float, float, float, int]] = [
    (15,  3,  3,  2),   # XS
    (40,  5,  5,  3),   # S
    (70,  8,  8,  4),   # M
    (120, 12, 12, 5),   # L
    (170, 16, 16, 5),   # XL
]
HOLE_CLEARANCE = 0.2  # mm added to hole radius for FDM tolerance


def load_mesh(path: Path) -> trimesh.Trimesh:
    """Load a single mesh from file; merge if Scene (e.g. 3MF)."""
    scene = trimesh.load(str(path), force="mesh", process=False)
    if isinstance(scene, trimesh.Scene):
        meshes = [g for g in scene.geometry.values() if isinstance(g, trimesh.Trimesh)]
        if not meshes:
            raise ValueError(f"No meshes found in {path}")
        if len(meshes) == 1:
            return meshes[0]
        return trimesh.util.concatenate(meshes)
    if isinstance(scene, trimesh.Trimesh):
        return scene
    raise ValueError(f"Unsupported loaded type: {type(scene)}")


def scale_mesh_to_build_volume(mesh: trimesh.Trimesh, vol_x: float, vol_y: float, vol_z: float):
    """
    Uniformly scale mesh to fit inside [vol_x, vol_y, vol_z] (preserving aspect ratio),
    then center it within that volume.
    Returns (scaled_mesh, uniform_scale_factor).
    """
    bounds = mesh.bounds
    lo, hi = bounds[0], bounds[1]
    dims = hi - lo
    per_axis = np.array([vol_x / dims[0], vol_y / dims[1], vol_z / dims[2]])
    per_axis = np.where(np.isfinite(per_axis) & (per_axis > 0), per_axis, 1.0)
    s = float(np.floor(np.min(per_axis)))
    scaled_dims = dims * s
    offset = (np.array([vol_x, vol_y, vol_z]) - scaled_dims) / 2.0
    T = np.eye(4)
    T[:3, :3] = np.diag([s, s, s])
    T[:3, 3] = -lo * s + offset
    return mesh.copy().apply_transform(T), s


def _mesh_in_box(mesh: trimesh.Trimesh, x0: float, x1: float, y0: float, y1: float, z0: float, z1: float) -> Optional[trimesh.Trimesh]:
    """Extract portion of mesh inside axis-aligned box [x0,x1] x [y0,y1] x [z0,z1] using 6 plane slices."""
    m = mesh
    for origin, normal in [
        ([x0, 0, 0], [1, 0, 0]),
        ([x1, 0, 0], [-1, 0, 0]),
        ([0, y0, 0], [0, 1, 0]),
        ([0, y1, 0], [0, -1, 0]),
        ([0, 0, z0], [0, 0, 1]),
        ([0, 0, z1], [0, 0, -1]),
    ]:
        m = intersections.slice_mesh_plane(m, plane_normal=normal, plane_origin=origin, cap=True)
        if m is None or len(m.vertices) == 0:
            return None
    return m if len(m.vertices) > 0 else None


def _cut_grid_cells(
    mesh: trimesh.Trimesh, vol: float, nx: int, ny: int, nz: int
) -> List[Tuple[int, int, int, trimesh.Trimesh]]:
    """Cut a pre-scaled mesh into an nx×ny×nz grid of cube cells of side `vol`."""
    out = []
    for i in range(nx):
        for j in range(ny):
            for k in range(nz):
                x0, x1 = i * vol, (i + 1) * vol
                y0, y1 = j * vol, (j + 1) * vol
                z0, z1 = k * vol, (k + 1) * vol
                cell = _mesh_in_box(mesh, x0, x1, y0, y1, z0, z1)
                if cell is not None and len(cell.vertices) > 0:
                    out.append((i, j, k, cell))
    return out


def cut_grid_by_cubes(mesh: trimesh.Trimesh, vol: float, n: int):
    """
    Mode 1: user specifies N cubes per axis.
    Scale mesh (integer, aspect-ratio-preserving) to fit inside N×N×N grid, then cut.
    Returns (cells, scale, n, n, n, scaled_mesh).
    """
    total = n * vol
    scaled, s = scale_mesh_to_build_volume(mesh, total, total, total)
    cells = _cut_grid_cells(scaled, vol, n, n, n)
    return cells, int(s), n, n, n, scaled


def cut_grid_by_scale(mesh: trimesh.Trimesh, vol: float, scale: int):
    """
    Mode 2: user specifies an integer scale factor.
    Apply that scale, compute the minimum nx/ny/nz needed per axis, then cut.
    Returns (cells, scale, nx, ny, nz, scaled_mesh).
    """
    bounds = mesh.bounds
    lo, hi = bounds[0], bounds[1]
    dims = hi - lo
    scaled_dims = dims * scale
    nx = max(1, math.ceil(scaled_dims[0] / vol))
    ny = max(1, math.ceil(scaled_dims[1] / vol))
    nz = max(1, math.ceil(scaled_dims[2] / vol))
    total = np.array([nx * vol, ny * vol, nz * vol])
    offset = (total - scaled_dims) / 2.0
    T = np.eye(4)
    T[:3, :3] = np.diag([scale, scale, scale])
    T[:3, 3] = -lo * scale + offset
    scaled_mesh = mesh.copy().apply_transform(T)
    cells = _cut_grid_cells(scaled_mesh, vol, nx, ny, nz)
    return cells, scale, nx, ny, nz, scaled_mesh


WALL_MARGIN = 1.0   # mm clearance between hole edge and model boundary


def _get_face_polygon(mesh: trimesh.Trimesh, axis: int, plane_val: float):
    """
    Return a Shapely geometry (Polygon/MultiPolygon) of the mesh cross-section
    at the given axis-aligned plane, projected to 2D. Returns None on failure.
    """
    from shapely.geometry import LineString
    from shapely.ops import polygonize, unary_union

    axes_2d = [a for a in range(3) if a != axis]
    plane_normal = np.zeros(3)
    plane_normal[axis] = 1.0
    plane_origin = np.zeros(3)
    plane_origin[axis] = plane_val
    try:
        lines_3d = trimesh.intersections.mesh_plane(mesh, plane_normal, plane_origin)
        if lines_3d is None or len(lines_3d) == 0:
            return None
        lines_2d = lines_3d[:, :, axes_2d]  # (N, 2, 2)
        shapely_lines = [LineString(seg) for seg in lines_2d]
        polys = list(polygonize(unary_union(shapely_lines)))
        if not polys:
            return None
        return unary_union(polys)
    except Exception:
        return None


def _find_fitting_peg(
    polygon, u: float, v: float, max_diam: float
) -> Optional[Tuple[float, float]]:
    """
    Find the largest peg (diameter <= max_diam) whose hole circle fits fully inside
    the face polygon with WALL_MARGIN clearance from the boundary.
    Returns (diameter, depth) or None if no size fits.
    """
    from shapely.geometry import Point

    pt = Point(u, v)
    for _, diam, depth, _ in reversed(PEG_SIZES):
        if diam > max_diam:
            continue
        required_r = diam / 2 + HOLE_CLEARANCE + WALL_MARGIN
        if polygon.contains(pt.buffer(required_r)):
            return diam, depth
    return None


def _get_peg_spec(face_shorter_side: float) -> Optional[Tuple[float, float, int]]:
    """Return (diameter, depth, max_pegs) for the largest fitting peg size, or None."""
    spec = None
    for min_side, diam, depth, max_pegs in PEG_SIZES:
        if face_shorter_side >= min_side:
            spec = (diam, depth, max_pegs)
    return spec


def _get_face_extent_2d(
    mesh: trimesh.Trimesh, axis: int, plane_value: float, tol: float = 0.5
) -> Optional[Tuple[float, float, float, float, List[int]]]:
    """Find 2D bounding box of mesh vertices lying on the given axis-aligned plane."""
    verts = mesh.vertices
    mask = np.abs(verts[:, axis] - plane_value) < tol
    if not np.any(mask):
        return None
    face_verts = verts[mask]
    axes_2d = [a for a in range(3) if a != axis]
    u_min = float(face_verts[:, axes_2d[0]].min())
    u_max = float(face_verts[:, axes_2d[0]].max())
    v_min = float(face_verts[:, axes_2d[1]].min())
    v_max = float(face_verts[:, axes_2d[1]].max())
    return u_min, u_max, v_min, v_max, axes_2d


def _compute_peg_positions(
    u_min: float, u_max: float, v_min: float, v_max: float,
    max_pegs: int, diam: float
) -> List[Tuple[float, float]]:
    """Compute up to max_pegs evenly spaced peg centers inside the face with edge clearance."""
    clearance = 2 * diam
    u_lo, u_hi = u_min + clearance, u_max - clearance
    v_lo, v_hi = v_min + clearance, v_max - clearance
    if u_lo >= u_hi or v_lo >= v_hi:
        return []
    cols = math.ceil(math.sqrt(max_pegs))
    rows = math.ceil(max_pegs / cols)
    positions = []
    for r in range(rows):
        for c in range(cols):
            if len(positions) >= max_pegs:
                break
            u = (u_lo + u_hi) / 2 if cols == 1 else u_lo + (u_hi - u_lo) * c / (cols - 1)
            v = (v_lo + v_hi) / 2 if rows == 1 else v_lo + (v_hi - v_lo) * r / (rows - 1)
            positions.append((u, v))
    return positions


def _measure_wall_thickness(
    mesh: trimesh.Trimesh, axis: int, plane_val: float,
    u: float, v: float, axes_2d: List[int], direction: int, tol: float = 0.5
) -> float:
    """
    Raycast inward from the face to measure wall thickness at a peg position.
    direction: +1 or -1, same convention as _subtract_holes.
    Returns the measured thickness in mm, or inf if no back wall is found.
    """
    origin = np.zeros(3)
    origin[axis] = plane_val - direction * tol
    origin[axes_2d[0]] = u
    origin[axes_2d[1]] = v

    ray_dir = np.zeros(3)
    ray_dir[axis] = float(direction)

    try:
        locs, _, _ = mesh.ray.intersects_location(
            ray_origins=origin.reshape(1, 3),
            ray_directions=ray_dir.reshape(1, 3),
        )
        if len(locs) == 0:
            return float("inf")
        dists = np.linalg.norm(locs - origin, axis=1)
        deep = dists[dists > 2 * tol]
        if len(deep) == 0:
            return float("inf")
        return float(deep.min()) - tol
    except Exception:
        return float("inf")


def _subtract_holes(
    mesh: trimesh.Trimesh, axis: int, plane_val: float,
    positions: List[Tuple[float, float]], axes_2d: List[int],
    hole_r: float, depths: List[float], direction: int
) -> trimesh.Trimesh:
    """
    Subtract cylindrical peg holes from mesh at the given face.
    direction: +1 = holes go into +axis from plane, -1 = into -axis.
    depths: per-position hole depths (may differ when wall is thinner than spec).
    """
    result = mesh.copy()
    for (u, v), depth in zip(positions, depths):
        center = np.zeros(3)
        center[axis] = plane_val + direction * depth / 2
        center[axes_2d[0]] = u
        center[axes_2d[1]] = v

        cyl = trimesh.creation.cylinder(radius=hole_r, height=depth + 0.01, sections=32)
        if axis == 0:
            cyl.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [0, 1, 0]))
        elif axis == 1:
            cyl.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0]))
        cyl.apply_translation(center)

        try:
            diff = trimesh.boolean.difference([result, cyl], engine='manifold')
            if diff is not None and len(diff.vertices) > 0:
                result = diff
        except Exception:
            pass
    return result


def add_peg_holes_to_grid(
    cells: List[Tuple[int, int, int, trimesh.Trimesh]], vol: float
) -> Tuple[List[Tuple[int, int, int, trimesh.Trimesh]], Dict[float, int]]:
    """
    Add peg holes to all internal cut faces between adjacent non-empty cells.
    Returns (cells_with_holes, peg_counts) where peg_counts is {diameter_mm: total_peg_count}.
    """
    cell_map = {(ci, cj, ck): idx for idx, (ci, cj, ck, _) in enumerate(cells)}
    meshes = [m.copy() for _, _, _, m in cells]
    peg_counts: Dict[float, int] = {}
    done_faces: set = set()

    for idx, (ci, cj, ck, _) in enumerate(cells):
        for axis, di, dj, dk in [(0, 1, 0, 0), (1, 0, 1, 0), (2, 0, 0, 1)]:
            ni, nj, nk = ci + di, cj + dj, ck + dk
            if (ni, nj, nk) not in cell_map:
                continue
            face_key = tuple(sorted([(ci, cj, ck), (ni, nj, nk)]))
            if face_key in done_faces:
                continue
            done_faces.add(face_key)

            nidx = cell_map[(ni, nj, nk)]
            plane_val = float((ci + di) * vol if axis == 0
                              else (cj + dj) * vol if axis == 1
                              else (ck + dk) * vol)

            ext = _get_face_extent_2d(meshes[idx], axis, plane_val)
            if ext is None:
                ext = _get_face_extent_2d(meshes[nidx], axis, plane_val)
            if ext is None:
                continue

            u_min, u_max, v_min, v_max, axes_2d = ext
            shorter = min(u_max - u_min, v_max - v_min)
            spec = _get_peg_spec(shorter)
            if spec is None:
                continue

            diam_spec, depth_spec, max_pegs = spec

            polygon = _get_face_polygon(meshes[idx], axis, plane_val)
            if polygon is None:
                polygon = _get_face_polygon(meshes[nidx], axis, plane_val)

            positions = _compute_peg_positions(u_min, u_max, v_min, v_max, max_pegs, diam_spec)
            if not positions:
                continue

            safety_margin = 0.5
            min_useful_depth = 1.0

            buckets: Dict[float, dict] = {}

            for pos in positions:
                u, v = pos

                if polygon is not None:
                    result = _find_fitting_peg(polygon, u, v, max_diam=diam_spec)
                    if result is None:
                        continue
                    fit_diam, fit_depth = result
                else:
                    fit_diam, fit_depth = diam_spec, depth_spec

                t_cur = _measure_wall_thickness(meshes[idx],  axis, plane_val, u, v, axes_2d, -1)
                t_nbr = _measure_wall_thickness(meshes[nidx], axis, plane_val, u, v, axes_2d, +1)
                d_cur = min(fit_depth, t_cur - safety_margin)
                d_nbr = min(fit_depth, t_nbr - safety_margin)
                if d_cur < min_useful_depth or d_nbr < min_useful_depth:
                    continue

                if fit_diam not in buckets:
                    buckets[fit_diam] = {"positions": [], "depths_cur": [], "depths_nbr": []}
                buckets[fit_diam]["positions"].append(pos)
                buckets[fit_diam]["depths_cur"].append(d_cur)
                buckets[fit_diam]["depths_nbr"].append(d_nbr)

            for fit_diam, data in buckets.items():
                hole_r = fit_diam / 2 + HOLE_CLEARANCE
                meshes[idx] = _subtract_holes(
                    meshes[idx], axis, plane_val,
                    data["positions"], axes_2d, hole_r, data["depths_cur"], -1
                )
                meshes[nidx] = _subtract_holes(
                    meshes[nidx], axis, plane_val,
                    data["positions"], axes_2d, hole_r, data["depths_nbr"], +1
                )
                peg_counts[fit_diam] = peg_counts.get(fit_diam, 0) + len(data["positions"])

    result = [(cells[i][0], cells[i][1], cells[i][2], meshes[i]) for i in range(len(cells))]
    return result, peg_counts


def create_peg_mesh(diameter: float, depth: float) -> trimesh.Trimesh:
    """Create a printable peg STL (solid cylinder, height = 2×depth for full engagement)."""
    return trimesh.creation.cylinder(radius=diameter / 2, height=2 * depth, sections=64)
