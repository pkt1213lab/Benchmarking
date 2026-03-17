# ArcGIS Pro Performance Benchmarking Tool

A standardized benchmarking tool for ArcGIS Pro that measures CPU geoprocessing performance using static, versioned datasets. Designed for comparing performance across machines or before/after a hardware upgrade.

---

## Overview

Each benchmark run executes a suite of geoprocessing operations across multiple iterations, records elapsed times, and saves a JSON results file. Results files can be loaded into the companion GitHub Pages site to visualize and compare performance across systems.

**Test categories**

| Category | Tests | Dataset |
|---|---|---|
| Vector Geoprocessing | Buffer, Clip, Intersect, Dissolve, Spatial Join, Union, Erase | Natural Earth 1:10m |
| Raster Analysis (SRTM) | Slope, Aspect, Hillshade, Focal Statistics | 6-tile SRTM 1-arc-second mosaic |
| Raster Analysis (LiDAR) | Slope, Aspect, Hillshade, Focal Statistics | USGS 3DEP QL2 1m DEM |
| LiDAR Processing | LAS Dataset → Raster | USGS 3DEP QL2 LAZ point clouds |

**Design principles**
- All inputs are loaded into the `in_memory` workspace before tests begin — disk I/O is not measured
- Each test runs 5 iterations (3 for Focal Statistics and LiDAR processing); mean, median, std dev, min, and max are reported
- Process priority is raised to High and the Windows power plan is switched to High Performance during the run to reduce variance
- ArcGIS parallel processing is set to n-1 cores, leaving one core for the OS

---

## Requirements

- **ArcGIS Pro** 3.0 or later
- **Python 3.x** — bundled with ArcGIS Pro (run from the ArcGIS Pro Python Command Prompt)
- **Spatial Analyst** extension — required for raster tests
- **3D Analyst** extension — required for LiDAR processing tests

---

## Quick Start

### 1. Download benchmark datasets

Download the dataset archives from the [GitHub Releases page](https://github.com/pkt1213lab/Benchmarking/releases/tag/v1.0) and extract them into the `BenchmarkData/` folder:

```
BenchmarkData/
├── Vector/
│   ├── ne_10m_admin_0_countries.shp   (and sidecar files)
│   └── ne_10m_admin_1_states_provinces.shp   (and sidecar files)
└── Raster/
    ├── n38_w106_1arc_v3.tif   (SRTM tiles — 6 total)
    ├── ...
    └── USGS_LPC_CO_WestCentral_2019_*.laz   (LiDAR point clouds)
```

**Archives to download:**

| File | Contents | Notes |
|---|---|---|
| `benchmark_vector_v1.0.zip` | Natural Earth shapefiles | Extract to `BenchmarkData/Vector/` |
| `benchmark_srtm_v1.0.zip` | 6 SRTM GeoTIFF tiles + metadata | Extract to `BenchmarkData/Raster/` |
| `benchmark_lidar_v1.0_part1.zip` | USGS 3DEP LAZ point clouds (part 1 of 3) | Extract to `BenchmarkData/Raster/` |
| `benchmark_lidar_v1.0_part2.zip` | USGS 3DEP LAZ point clouds (part 2 of 3) | Extract to `BenchmarkData/Raster/` |
| `benchmark_lidar_v1.0_part3.zip` | USGS 3DEP LAZ point clouds (part 3 of 3) | Extract to `BenchmarkData/Raster/` |

> **Note:** The LiDAR archives are large (~1.5 GB each). If you only want to run vector and SRTM raster tests, you can skip them and select **Raster** or **Vector** at the test selection prompt.

### 2. Run the benchmark

Open the **ArcGIS Pro Python Command Prompt** and run:

```bash
cd C:\Users\YourName\Documents\PythonScripts\Benchmarking
python Benchmarking.py
```

You will be prompted to select which tests to run:

```
Select tests to run:
  1. All (vector + raster)  [default]
  2. Vector only
  3. Raster only
Enter choice [1-3, or press Enter for default]:
```

You can also pass the selection as a command-line argument to skip the prompt:

```bash
python Benchmarking.py --tests all
python Benchmarking.py --tests vector
python Benchmarking.py --tests raster
```

### 3. First run — LiDAR preparation

On the first run the script will:
1. Build a LAS Dataset (`lidar.lasd`) from all LAZ files — this is a one-time step
2. Convert the LAS Dataset to a 1m DEM (`lidar_dem_1m.tif`) — also one-time

These outputs are cached and reused on subsequent runs. If you add new LAZ files to the `Raster/` folder, the script detects the change and rebuilds them automatically.

---

## Results

Results are saved to the `results/` folder as timestamped JSON files:

```
results/benchmark_20260316_210000.json
```

### JSON structure

```json
{
  "benchmark_id": "20260316_210000",
  "timestamp": "2026-03-16T21:00:00",
  "dataset_version": "v1.0",
  "iterations": 5,
  "cores_used": 19,
  "system_info": {
    "cpu_model": "12th Gen Intel(R) Core(TM) i9-12900HK",
    "cpu_cores": 20,
    "ram_gb": 64.0,
    "gpu_model": "NVIDIA RTX A2000 Laptop GPU",
    "arcgis_version": "3.6",
    "arcgis_build": "59527"
  },
  "results": {
    "vector_geoprocessing": {
      "buffer_100m_countries": {
        "trials": [56.1, 56.2, 56.0, 56.3, 56.2],
        "mean": 56.16,
        "median": 56.2,
        "std_dev": 0.11,
        "min": 56.0,
        "max": 56.3,
        "status": "success"
      }
    }
  },
  "total_time_seconds": 2615.31
}
```

### Comparing results across machines

1. Run the benchmark on each machine and commit the JSON files to `results/`
2. Push to GitHub
3. Open the [benchmark results site](https://pkt1213lab.github.io/Benchmarking) — it auto-loads all JSON files and renders comparison charts

---

## Results Visualization (GitHub Pages)

The `docs/index.html` site loads result JSON files directly from the repository using the GitHub Contents API and renders grouped bar charts using Chart.js. No server required.

To add a result to the site:
1. Run the benchmark and confirm the JSON was saved to `results/`
2. Commit the JSON file and push to GitHub — the site updates automatically

---

## Datasets

### Vector — Natural Earth 1:10m

| Dataset | Features | Source |
|---|---|---|
| `ne_10m_admin_0_countries.shp` | ~250 country polygons | [naturalearthdata.com](https://www.naturalearthdata.com) |
| `ne_10m_admin_1_states_provinces.shp` | ~4,600 state/province polygons | [naturalearthdata.com](https://www.naturalearthdata.com) |

### Raster — SRTM 1-Arc-Second Void Filled

6 contiguous GeoTIFF tiles covering western Colorado/eastern Utah (~30m resolution, WGS84).

| Tile | Coverage |
|---|---|
| n38_w106, n38_w107, n38_w108 | 38°N row |
| n39_w106, n39_w107, n39_w108 | 39°N row |

Source: [USGS EarthExplorer](https://earthexplorer.usgs.gov) — SRTM 1 Arc-Second Global

### LiDAR — USGS 3DEP QL2

65 LAZ tiles from the USGS LPC CO WestCentral 2019 project covering the same geographic extent as the SRTM tiles.

- **Quality Level:** QL2 (≥ 2 pts/m², average point spacing ~0.7m)
- **Output DEM:** 1m cell size (standard for QL2)
- **Coordinate system:** NAD83

Source: [USGS 3DEP LidarExplorer](https://apps.nationalmap.gov/lidar-explorer)

---

## Dataset Checksums (SHA256)

| File | SHA256 |
|---|---|
| `benchmark_vector_v1.0.zip` | `944B2A7046FAED445A27BE1A3A01D7F57537B4702857738083993AFBAA29BACA` |
| `benchmark_srtm_v1.0.zip` | `2691282A64CEDAD39E4A886C3A6CB62355AAF00B96134C6A64FDE7DE8B0699AB` |
| `benchmark_lidar_v1.0_part1.zip` | `7CAD1B9F40C4DF8770E44BE1AAEC553DA192896DB4334B2772C6E6912F87C02B` |
| `benchmark_lidar_v1.0_part2.zip` | `85038D013D143DC50B9248A6E32550FA12F2967C26E327EDA9A02F3D5ADDECBC` |
| `benchmark_lidar_v1.0_part3.zip` | `2E62B2A7B8996C0516B09C2B96C21FDD3AF921828DC7C2DF8F5826728A93BE2D` |

Verify on Windows:
```powershell
Get-FileHash benchmark_vector_v1.0.zip -Algorithm SHA256
```

---

## Configuration

Edit the constants at the top of `main()` in `Benchmarking.py`:

```python
ITERATIONS      = 5      # trials per test (FocalStatistics and LiDAR use 3)
DATASET_VERSION = "v1.0" # which dataset version to validate against
```

---

## Architecture

| Class | Responsibility |
|---|---|
| `SystemProfiler` | Detects CPU, RAM, GPU, ArcGIS version/build |
| `DatasetManager` | Locates datasets, builds LiDAR derived products, exposes input paths |
| `BenchmarkTest` | Base class — iteration loop, timing, statistics, in_memory cleanup |
| `BenchmarkManager` | Orchestrates the full run, system config, result output |

### Adding a new test

Create a subclass of `BenchmarkTest`, implement `_execute()`, and add it to the appropriate `_run_*_tests()` method in `BenchmarkManager`:

```python
class ViewshedTest(BenchmarkTest):

    def __init__(self, iterations: int = 3):
        super().__init__("Raster Viewshed", iterations)
        self._outputs = ["viewshed_output"]

    def _execute(self, input_raster: str) -> bool:
        # Define an observer point at the centre of the raster extent
        observer = os.path.join(self.workspace, f"observer_{self._uid}")
        # ... create observer feature class ...
        output = self._out("viewshed_output")
        arcpy.sa.Viewshed2(input_raster, observer).save(output)
        return arcpy.Exists(output)
```

---

## Troubleshooting

**"Dataset setup failed"**
Datasets are not in the expected location. Check that `BenchmarkData/Vector/` and `BenchmarkData/Raster/` exist and contain the required files.

**"Could not check out Spatial extension"**
Ensure Spatial Analyst is licensed in your ArcGIS Pro installation. Raster tests will be skipped if unavailable.

**Raster tests all FAILED**
This is most commonly caused by the ArcGIS in_memory raster locking bug (ERROR 000871/000872). The script works around this using unique output names per test instance — if you see this error, check that `arcpy.env.overwriteOutput = True` is set and that no other ArcGIS Pro session is running.

**LiDAR times much shorter than expected**
The script caches `lidar.lasd` and `lidar_dem_1m.tif`. If you added new LAZ tiles but the times did not change, delete those two files from `BenchmarkData/Raster/` and re-run — the script will detect the new tile count and rebuild automatically.

**High variance between runs**
Close other applications before benchmarking. The script sets High Performance power plan and High process priority automatically, but background system activity (antivirus scans, Windows Update, etc.) can still affect results.

---

## License

Provided as-is for performance testing and research purposes.

## Author

Created for standardized ArcGIS Pro performance benchmarking across system upgrades.
Repository: [github.com/pkt1213lab/Benchmarking](https://github.com/pkt1213lab/Benchmarking)
