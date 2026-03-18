# 3D Model Cutter

Have a model too big for your printer? This tool automatically scales it to fit your build volume, cuts it into a grid of printable pieces, and **adds alignment peg holes on every cut face** so the parts snap together cleanly after printing.

Runs as a local web app — open it in any browser, no slicer knowledge needed.

---

## Use cases

**Large display models and props**
You found a detailed statue, helmet, or prop online but it's 3x the size of your print bed. Paste in your build volume, set a scale factor, and get a ZIP of all pieces with peg holes pre-drilled — ready to slice and glue up.

**Cosplay armor and wearables**
Big chest plates, shields, or helmets that need to be printed in sections and assembled. The peg system keeps parts aligned while the glue sets.

**Architectural models and dioramas**
Scale models of buildings or terrain pieces that need to be printed in tiles and assembled into a larger scene.

**Splitting a model to print hollow or with better orientation**
Cut a figurine or bust horizontally at the waist so each half can be printed face-up with no supports on the flat faces.

**Shipping or storing a large print flat**
Cut a large decorative piece into sections that fit in a flat-rate box or a drawer, then reassemble at the destination.

---

## What makes this different from PrusaSlicer's cut tool

- Cuts the **entire model into a grid in one step** — not one plane at a time
- Automatically adds **alignment peg holes** on all mating faces
- Outputs a **peg STL** to print the connectors + a manifest of how many pegs you need per size
- **Web UI** — works on any OS, no slicer required
- Supports STL, OBJ, 3MF, PLY, GLB, GLTF, DAE and more

---

## How to use

1. Drop a 3D file (STL, OBJ, 3MF, PLY, GLB…) onto the upload zone or click to choose one.
2. Enter your printer's build volume (cube side in mm) and choose a grid mode:
   - **Cubes per axis** — enter N (e.g. N=2 gives a 2×2×2 grid = 8 pieces). Model is scaled to fit and cut at cube boundaries.
   - **Scale factor** — enter an integer scale; cubes per axis are computed automatically.
3. Click **Cut model** and download the ZIP.
4. Open the STL parts in your slicer and print. Use the included `peg_Xmm.stl` to print the alignment pegs.

---

## Setup on a new machine

### Prerequisites

| Requirement | macOS | Windows |
|-------------|-------|---------|
| Python 3.9+ | [python.org](https://www.python.org/downloads/) or `brew install python` | [python.org](https://www.python.org/downloads/) — check **"Add to PATH"** during install |
| pip | bundled with Python | bundled with Python |
| Homebrew | [brew.sh](https://brew.sh) | not needed |

---

### Step 1 — Clone the project

**macOS / Windows:**
```bash
git clone https://github.com/YOUR_USERNAME/3dmesh.git
cd 3dmesh
```

> If you don't have Git, download the ZIP from GitHub and extract it, then `cd` into the folder.

---

### Step 2 — Install system dependency

**macOS** — `rtree` requires the `spatialindex` C library:
```bash
brew install spatialindex
```

**Windows** — No extra step needed. `pip install rtree` handles everything automatically.

---

### Step 3 — Create a virtual environment

**macOS:**
```bash
python3 -m venv venv
source venv/bin/activate
```

**Windows (Command Prompt):**
```bat
python -m venv venv
venv\Scripts\activate.bat
```

**Windows (PowerShell):**
```powershell
python -m venv venv
venv\Scripts\Activate.ps1
```

> If PowerShell blocks the activation script, run this once:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

---

### Step 4 — Install Python dependencies

```bash
pip install -r requirements.txt
```

This installs: `flask`, `trimesh`, `numpy`, `shapely`, `rtree`, `manifold3d`, and others.

---

### Step 5 — Run the app

**macOS:**
```bash
python3 app.py
```

**Windows:**
```bat
python app.py
```

You should see:
```
* Running on http://127.0.0.1:5050
```

---

### Step 6 — Open in your browser

Go to **http://localhost:5050**

---

## Troubleshooting

| Problem | macOS fix | Windows fix |
|---------|-----------|-------------|
| `Missing dependency: rtree` | `brew install spatialindex` then `pip install rtree` | `pip install rtree` (no extra step) |
| `Port 5050 is already in use` | `lsof -ti:5050 \| xargs kill -9` | `netstat -ano \| findstr :5050` then `taskkill /PID <pid> /F` |
| `python3: command not found` | `brew install python` or [python.org](https://www.python.org/downloads/) | Reinstall Python and check **"Add to PATH"** |
| PowerShell won't activate venv | — | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| Cut takes a long time | Normal for large or complex meshes — the button pulses while processing | Same |

---

## Contributing

Bug reports, feature ideas, and pull requests are welcome. Open an issue to start a discussion.
