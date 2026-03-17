# =============================================================================
# ArcGIS Pro Performance Benchmarking Tool
# =============================================================================
# Measures CPU (and optionally GPU) performance across geoprocessing operations
# using static, versioned datasets so results are reproducible and comparable
# across machines and over time (e.g. pre/post hardware upgrade).
#
# Test categories
#   vector_geoprocessing      – Buffer, Clip, Intersect, Dissolve, SpatialJoin,
#                               Union, Erase on Natural Earth vector data
#   raster_analysis           – Slope, Aspect, Hillshade, FocalStatistics on
#                               6-tile SRTM 1-arc-second mosaic (~30m)
#   raster_analysis_lidar_1m  – Same raster tests on the LiDAR 1m DEM for
#                               direct comparison with the SRTM dataset
#   lidar_processing          – LAS Dataset → Raster conversion (point-cloud
#                               binning, highly CPU-intensive)
#
# Datasets (place in BenchmarkData/Vector/ and BenchmarkData/Raster/)
#   ne_10m_admin_0_countries.shp      Natural Earth 1:10m country polygons
#   ne_10m_admin_1_states_provinces.shp Natural Earth 1:10m states/provinces
#   n??_w???_1arc_v3.tif              SRTM 1-arc-second void-filled GeoTIFFs
#   *.laz                             USGS 3DEP QL2 LiDAR point clouds
#
# Requirements
#   ArcGIS Pro 3.0+  |  Spatial Analyst extension  |  3D Analyst extension
#   Python 3.x (bundled with ArcGIS Pro)
# =============================================================================

import argparse
import arcpy
import os
import sys
import json
import time
import platform
import statistics
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# =============================================================================
# System Profiler
# =============================================================================

# Collects hardware and software metadata written into every result JSON so
# that results files are self-describing and machine comparisons are unambiguous.
class SystemProfiler:

    def __init__(self):
        self.profile = self._gather_system_info()

    # Build the full profile dict once at construction time.
    def _gather_system_info(self) -> Dict:
        logger.info("Profiling system hardware and software...")

        gpu_model   = self._get_gpu_model()
        arcgis_info = self._get_arcgis_info()

        profile = {
            "cpu_model":       self._get_cpu_model(),
            "cpu_cores":       os.cpu_count() or 0,
            "ram_gb":          self._get_ram_gb(),
            "gpu_model":       gpu_model,
            "gpu_available":   self._is_gpu_available(gpu_model),
            "arcgis_version":  arcgis_info["version"],
            "arcgis_build":    arcgis_info["build"],
            "python_version":  platform.python_version(),
            "platform":        platform.platform(),
            "machine":         platform.machine(),
        }

        logger.info(f"System: {profile['cpu_model']}, {profile['cpu_cores']} cores, "
                    f"{profile['ram_gb']}GB RAM")
        logger.info(f"ArcGIS Pro: {profile['arcgis_version']} "
                    f"(build {profile['arcgis_build']})")
        if profile['gpu_available']:
            logger.info(f"GPU: {profile['gpu_model']}")
        else:
            logger.info(f"GPU: {profile['gpu_model']} "
                        f"(not available for ArcGIS processing)")

        return profile

    # Read CPU name from the registry — more reliable than platform.processor()
    # which returns architecture strings on some Windows configurations.
    def _get_cpu_model(self) -> str:
        try:
            if platform.system() == "Windows":
                import winreg
                key = winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
                )
                return winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
        except Exception as e:
            logger.warning(f"Could not detect CPU model: {e}")
        return platform.processor() or "Unknown CPU"

    # Use the Windows memory status struct directly — no psutil dependency.
    def _get_ram_gb(self) -> float:
        try:
            if platform.system() == "Windows":
                import ctypes

                class MEMORYSTATUSEX(ctypes.Structure):
                    _fields_ = [
                        ("dwLength",                ctypes.c_ulong),
                        ("dwMemoryLoad",             ctypes.c_ulong),
                        ("ullTotalPhys",             ctypes.c_ulonglong),
                        ("ullAvailPhys",             ctypes.c_ulonglong),
                        ("ullTotalPageFile",         ctypes.c_ulonglong),
                        ("ullAvailPageFile",         ctypes.c_ulonglong),
                        ("ullTotalVirtual",          ctypes.c_ulonglong),
                        ("ullAvailVirtual",          ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual",  ctypes.c_ulonglong),
                    ]

                mem = MEMORYSTATUSEX()
                mem.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
                ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem))
                return round(mem.ullTotalPhys / (1024 ** 3), 2)
        except Exception as e:
            logger.warning(f"Could not detect RAM: {e}")
        return 0.0

    # wmic was removed from Windows 11 24H2, so use PowerShell CIM instead.
    # Multiple adapters (e.g. Intel iGPU + NVIDIA dGPU) are joined with ';'.
    def _get_gpu_model(self) -> str:
        try:
            if platform.system() == "Windows":
                import subprocess
                result = subprocess.run(
                    ['powershell', '-Command',
                     '(Get-CimInstance -ClassName Win32_VideoController)'
                     '.Name -join ";"'],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode == 0:
                    names = [n.strip() for n in result.stdout.strip().split(';')
                             if n.strip()]
                    if names:
                        return '; '.join(names)
        except Exception as e:
            logger.warning(f"Could not detect GPU: {e}")
        return "Unknown GPU"

    # ArcGIS GPU acceleration targets NVIDIA CUDA and AMD OpenCL adapters.
    # Intel integrated graphics is present on most laptops but is not used
    # by ArcGIS geoprocessing tools.
    def _is_gpu_available(self, gpu_model: str) -> bool:
        lower = gpu_model.lower()
        return any(k in lower for k in ('nvidia', 'amd', 'radeon', 'geforce'))

    # GetInstallInfo returns a dict with 'Version' (e.g. '3.6') and
    # 'BuildNumber' (e.g. '59527').  Both are stored separately so the
    # results JSON can be filtered/sorted by either field.
    def _get_arcgis_info(self) -> Dict:
        try:
            info = arcpy.GetInstallInfo()
            return {
                "version": info.get("Version", "Unknown"),
                "build":   info.get("BuildNumber", "Unknown"),
            }
        except Exception as e:
            logger.warning(f"Could not get ArcGIS version: {e}")
        return {"version": "Unknown", "build": "Unknown"}


# =============================================================================
# Dataset Manager
# =============================================================================

# Locates benchmark datasets on disk, prepares derived products (LAS Dataset,
# LiDAR DEM), and exposes helper methods used by BenchmarkManager to load
# inputs into the in_memory workspace before tests begin.
class DatasetManager:

    # Dataset registry — archives are split by type because raster/LAZ files
    # are already compressed and gain nothing from further compression.
    # URLs follow the GitHub Releases pattern; confirm them after uploading.
    # Checksums are SHA256, generated with PowerShell Get-FileHash.
    DATASETS = {
        "v1.0": {
            "description": "Benchmark datasets v1.0 — Natural Earth + SRTM + USGS 3DEP LiDAR",
            "files": [
                {
                    "name":       "benchmark_vector_v1.0.zip",
                    "url":        "https://github.com/pkt1213lab/Benchmarking/releases/download/v1.0/benchmark_vector_v1.0.zip",
                    "checksum":   "944B2A7046FAED445A27BE1A3A01D7F57537B4702857738083993AFBAA29BACA",
                    "extract_to": "Vector",
                },
                {
                    "name":       "benchmark_srtm_v1.0.zip",
                    "url":        "https://github.com/pkt1213lab/Benchmarking/releases/download/v1.0/benchmark_srtm_v1.0.zip",
                    "checksum":   "2691282A64CEDAD39E4A886C3A6CB62355AAF00B96134C6A64FDE7DE8B0699AB",
                    "extract_to": "Raster",
                },
                {
                    "name":       "benchmark_lidar_v1.0_part1.zip",
                    "url":        "https://github.com/pkt1213lab/Benchmarking/releases/download/v1.0/benchmark_lidar_v1.0_part1.zip",
                    "checksum":   "7CAD1B9F40C4DF8770E44BE1AAEC553DA192896DB4334B2772C6E6912F87C02B",
                    "extract_to": "Raster",
                },
                {
                    "name":       "benchmark_lidar_v1.0_part2.zip",
                    "url":        "https://github.com/pkt1213lab/Benchmarking/releases/download/v1.0/benchmark_lidar_v1.0_part2.zip",
                    "checksum":   "85038D013D143DC50B9248A6E32550FA12F2967C26E327EDA9A02F3D5ADDECBC",
                    "extract_to": "Raster",
                },
                {
                    "name":       "benchmark_lidar_v1.0_part3.zip",
                    "url":        "https://github.com/pkt1213lab/Benchmarking/releases/download/v1.0/benchmark_lidar_v1.0_part3.zip",
                    "checksum":   "2E62B2A7B8996C0516B09C2B96C21FDD3AF921828DC7C2DF8F5826728A93BE2D",
                    "extract_to": "Raster",
                },
            ],
        }
    }

    def __init__(self, data_dir: Path, version: str = "v1.0"):
        self.data_dir   = data_dir
        self.version    = version
        self.vector_dir = data_dir / "Vector"
        self.raster_dir = data_dir / "Raster"

    # Entry point called by BenchmarkManager before tests run.
    # Ensures directories exist, builds LiDAR derived products if needed,
    # then validates that the core vector and raster files are present.
    def setup(self) -> bool:
        logger.info(f"Setting up benchmark datasets (version {self.version})...")

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.vector_dir.mkdir(exist_ok=True)
        self.raster_dir.mkdir(exist_ok=True)

        # Build LASD and 1m DEM from LAZ files before the main validation step
        # so that get_lidar_dem() returns a valid path during the benchmark run.
        self._prepare_lidar()

        if self._datasets_exist():
            logger.info("Datasets found, validating checksums...")
            if self._validate_checksums():
                logger.info("Dataset validation successful")
                return True
            else:
                logger.warning("Dataset validation failed, re-downloading...")

        return self._download_datasets()

    # Two-step LiDAR preparation — both outputs are created once and reused
    # across benchmark runs unless new LAZ files are detected.
    #
    # Step 1  Create lidar.lasd  — the input consumed by LiDARToRasterTest
    #         so that test measures point-cloud-to-raster conversion time,
    #         not file discovery overhead.
    #
    # Step 2  Create lidar_dem_1m.tif  — a pre-built DEM loaded into
    #         in_memory for raster_analysis_lidar_1m tests (Slope, Aspect,
    #         Hillshade, FocalStatistics), mirroring how SRTM tiles are used.
    def _prepare_lidar(self):
        laz_files = sorted(self.raster_dir.glob("*.laz"))
        if not laz_files:
            return

        lasd_path = self.raster_dir / "lidar.lasd"
        dem_path  = self.raster_dir / "lidar_dem_1m.tif"

        # Rebuild the LASD (and downstream DEM) if the file count has changed,
        # which happens when new LAZ tiles are added to the Raster folder.
        lasd_stale = not lasd_path.exists()
        if lasd_path.exists():
            try:
                desc = arcpy.Describe(str(lasd_path))
                if desc.pointFileCount != len(laz_files):
                    logger.info(
                        f"LAZ count changed ({desc.pointFileCount} → "
                        f"{len(laz_files)}), rebuilding LAS Dataset..."
                    )
                    lasd_path.unlink()
                    # Remove all sidecar files associated with the old DEM
                    # (.tif.aux.xml, .tif.ovr, .tif.xml, .tfw, etc.)
                    for p in dem_path.parent.glob(f"{dem_path.name}*"):
                        p.unlink(missing_ok=True)
                    dem_path.with_suffix(".tfw").unlink(missing_ok=True)
                    lasd_stale = True
            except Exception:
                lasd_stale = True

        if lasd_stale:
            logger.info(f"Creating LAS Dataset from {len(laz_files)} LAZ file(s)...")
            try:
                arcpy.management.CreateLasDataset(
                    input=[str(f) for f in laz_files],
                    out_las_dataset=str(lasd_path),
                    # compute_stats=True builds spatial index and point statistics
                    # so that LasDatasetToRaster does not need to scan all files
                    # at the start of each benchmark iteration.
                    compute_stats=True
                )
                logger.info(f"  LAS Dataset created: {lasd_path.name}")
            except Exception as e:
                logger.error(f"LAS Dataset creation failed: {e}")
                return
        else:
            logger.info(f"LAS Dataset already exists: {lasd_path.name}")

        if not dem_path.exists():
            logger.info("Converting LAS Dataset to 1m DEM (one-time, not benchmarked)...")
            try:
                # Cell size of 1m matches the QL2 standard output resolution.
                # QL2 spec is ≥2 pts/m² (avg point spacing ~0.7m), so 1m cells
                # reliably contain at least one ground return for BINNING AVERAGE.
                arcpy.conversion.LasDatasetToRaster(
                    in_las_dataset=str(lasd_path),
                    out_raster=str(dem_path),
                    value_field="ELEVATION",
                    interpolation_type="BINNING AVERAGE LINEAR",
                    data_type="FLOAT",
                    sampling_type="CELLSIZE",
                    sampling_value=1
                )
                logger.info(f"  LiDAR DEM saved: {dem_path.name}")
            except Exception as e:
                logger.error(f"LiDAR DEM creation failed: {e}")
                # Remove any partial output so the next run attempts a fresh build
                for p in dem_path.parent.glob(f"{dem_path.name}*"):
                    p.unlink(missing_ok=True)
                dem_path.with_suffix(".tfw").unlink(missing_ok=True)
        else:
            logger.info(f"LiDAR DEM already exists: {dem_path.name}")

    # Minimum check — at least one country shapefile and one raster tile present.
    def _datasets_exist(self) -> bool:
        vector_ok = (self.vector_dir / "ne_10m_admin_0_countries.shp").exists()
        raster_ok = any(self.raster_dir.glob("*.tif"))
        return vector_ok and raster_ok

    # Checksum validation is a placeholder until real dataset archives are
    # hosted and their SHA256 hashes are known.
    def _validate_checksums(self) -> bool:
        logger.info("Checksum validation not yet implemented")
        return True

    # Prints instructions when datasets are not found — no silent failure.
    def _download_datasets(self) -> bool:
        dataset_info = self.DATASETS.get(self.version)
        if not dataset_info:
            logger.error(f"No dataset configuration for version {self.version}")
            return False

        logger.info("=" * 60)
        logger.info("DATASET DOWNLOAD REQUIRED")
        logger.info("=" * 60)
        logger.info(f"Version: {self.version}")
        logger.info(f"Description: {dataset_info['description']}")
        logger.info(f"\nDatasets expected in: {self.data_dir}")
        logger.info("\nNOTE: Dataset URLs are not yet configured.")
        logger.info("To use this benchmark tool:")
        logger.info("  1. Prepare vector and raster datasets (see README)")
        logger.info("  2. Host archives on GitHub Releases or cloud storage")
        logger.info("  3. Update DATASETS with real URLs and SHA256 checksums")
        logger.info(f"\nFor manual setup, place files in:")
        logger.info(f"  Vector: {self.vector_dir}")
        logger.info(f"  Raster: {self.raster_dir}")
        logger.info("=" * 60)
        return False

    # Return path to a named vector file, or None if it does not exist.
    def get_vector_data(self, name: str) -> Optional[str]:
        path = self.vector_dir / name
        return str(path) if path.exists() else None

    # Return path to a named raster file, or None if it does not exist.
    def get_raster_data(self, name: str) -> Optional[str]:
        path = self.raster_dir / name
        return str(path) if path.exists() else None

    # Return all SRTM GeoTIFF tile paths, explicitly excluding the derived
    # LiDAR DEM so it is not accidentally included in the SRTM mosaic.
    def get_raster_tiles(self) -> List[str]:
        return sorted(
            str(p) for p in self.raster_dir.glob("*.tif")
            if p.name != "lidar_dem_1m.tif"
        )

    # Return path to the pre-built LiDAR 1m DEM, or None if not yet created.
    def get_lidar_dem(self) -> Optional[str]:
        path = self.raster_dir / "lidar_dem_1m.tif"
        return str(path) if path.exists() else None

    # Return path to the LAS Dataset, or None if not yet created.
    def get_las_dataset(self) -> Optional[str]:
        path = self.raster_dir / "lidar.lasd"
        return str(path) if path.exists() else None


# =============================================================================
# Benchmark Test Base Class
# =============================================================================

# Base class for all benchmark tests.  Handles the iteration loop, timing,
# statistics, and cleanup.  Subclasses only need to implement _execute().
#
# Output naming strategy
# ----------------------
# ArcGIS cannot delete or overwrite raster datasets in the in_memory workspace
# while they are referenced by any internal object (ERROR 000871 / 000872).
# To work around this each test instance generates a short random UID (_uid)
# at construction time.  Every output path includes both the UID and the
# iteration index, e.g. "in_memory/slope_output_a3f1c2_0", guaranteeing that:
#   (a) no two iterations of the same test collide, and
#   (b) no two test instances (e.g. SRTM slope and LiDAR slope) collide even
#       if their cleanup of the previous run silently failed.
# Previous-iteration outputs are deleted before each new iteration starts so
# that in_memory does not accumulate stale rasters over a long run.
class BenchmarkTest:

    def __init__(self, name: str, iterations: int = 5):
        self.name       = name
        self.iterations = iterations
        self.workspace  = "in_memory"
        # Base output names registered by each subclass.  The UID and iteration
        # index are appended at runtime via _out() / _clear_iteration_outputs().
        self._outputs: List[str] = []
        self._iter = 0  # updated by run() before each _execute() call
        # 6-character hex tag unique to this test instance — see class docblock.
        self._uid = uuid.uuid4().hex[:6]

    # Run _execute() self.iterations times, collect elapsed times, and return
    # a statistics dict.  Input arguments are passed through to _execute().
    def run(self, *args, **kwargs) -> Dict:
        logger.info(f"\nRunning test: {self.name} ({self.iterations} iterations)")

        trials = []
        for i in range(self.iterations):
            self._iter = i
            logger.info(f"  Iteration {i+1}/{self.iterations}...")

            # Delete the previous iteration's outputs before writing new ones.
            # This is the primary defence against the in_memory locking bug —
            # outputs from iteration N-1 are no longer referenced by the time
            # iteration N starts, so deletion succeeds reliably.
            if i > 0:
                self._clear_iteration_outputs(i - 1)

            start_time = time.perf_counter()
            try:
                success  = self._execute(*args, **kwargs)
                end_time = time.perf_counter()

                if success:
                    elapsed = end_time - start_time
                    trials.append(elapsed)
                    logger.info(f"    Completed in {elapsed:.2f}s")
                else:
                    logger.warning(f"    Trial {i+1} failed (execute returned False)")
            except Exception as e:
                import traceback
                logger.error(f"    Error in trial {i+1}: {str(e)}")
                # Full traceback at DEBUG so it is visible with -v / logging.DEBUG
                # without cluttering the normal INFO output during a run.
                logger.debug(traceback.format_exc())

        # Clean up the final iteration's outputs after the loop ends.
        self._clear_iteration_outputs(self.iterations - 1)

        if trials:
            result = {
                "trials":  [round(t, 3) for t in trials],
                "mean":    round(statistics.mean(trials), 3),
                "median":  round(statistics.median(trials), 3),
                # stdev requires at least 2 samples; return 0 for a single trial.
                "std_dev": round(statistics.stdev(trials), 3) if len(trials) > 1 else 0,
                "min":     round(min(trials), 3),
                "max":     round(max(trials), 3),
                "status":  "success",
            }
            logger.info(f"  Results: mean={result['mean']}s, "
                        f"std_dev={result['std_dev']}s")
        else:
            result = {
                "trials": [], "mean": 0, "median": 0, "std_dev": 0,
                "min": 0, "max": 0, "status": "failed",
            }
            logger.error("  All trials failed")

        return result

    # Override in subclasses to perform one iteration of the test.
    # Must return True on success, False on failure (no exception required).
    def _execute(self, *_args, **_kwargs) -> bool:
        raise NotImplementedError("Subclasses must implement _execute()")

    # Return the in_memory path for a named output, incorporating the instance
    # UID and current iteration index to guarantee uniqueness.
    # Example:  _out("slope_output")  →  "in_memory/slope_output_a3f1c2_2"
    def _out(self, name: str) -> str:
        return os.path.join(self.workspace, f"{name}_{self._uid}_{self._iter}")

    # Delete all registered outputs for a given iteration index.
    # Failures are silently ignored — the UID strategy means a failed cleanup
    # only wastes a small amount of in_memory space rather than blocking the
    # next iteration.
    def _clear_iteration_outputs(self, iter_idx: int):
        for name in self._outputs:
            path = os.path.join(self.workspace, f"{name}_{self._uid}_{iter_idx}")
            try:
                if arcpy.Exists(path):
                    arcpy.management.Delete(path)
            except Exception:
                pass


# =============================================================================
# Vector Geoprocessing Tests
# =============================================================================

# Buffer all features by 100m with no dissolve.
# Runs against both the countries layer (~250 features) and the
# states/provinces layer (~4,600 features) to exercise geodesic
# buffering at different feature counts.
# Note: geodesic buffers on complex coastlines in WGS84 are CPU-intensive
# (~56s for countries, ~105s for states) and dominate the vector runtime.
class BufferTest(BenchmarkTest):

    def __init__(self, iterations: int = 5):
        super().__init__("Vector Buffer (100m)", iterations)
        self._outputs = ["buffer_output"]

    def _execute(self, input_fc: str) -> bool:
        output = self._out("buffer_output")
        # NONE dissolve keeps individual feature buffers separate, avoiding
        # the extra geometry union pass that ALL or LIST dissolve would trigger.
        arcpy.analysis.Buffer(input_fc, output, "100 Meters",
                              dissolve_option="NONE")
        return arcpy.Exists(output)


# Clip the countries layer to the Western Hemisphere using a manually
# constructed bounding polygon.  Tests clip performance on a global dataset.
class ClipTest(BenchmarkTest):

    def __init__(self, iterations: int = 5):
        super().__init__("Vector Clip (Western Hemisphere)", iterations)
        self._outputs = ["clip_boundary", "clip_output"]

    def _execute(self, input_fc: str) -> bool:
        clip_fc = self._out("clip_boundary")
        # CreateFeatureclass expects just the base name, not the full path,
        # so extract it from the UID-qualified path via os.path.basename().
        arcpy.management.CreateFeatureclass(
            self.workspace, os.path.basename(clip_fc), "POLYGON",
            spatial_reference=arcpy.SpatialReference(4326)
        )
        ring = arcpy.Array([
            arcpy.Point(-180, -90), arcpy.Point(0, -90),
            arcpy.Point(0,    90),  arcpy.Point(-180, 90),
            arcpy.Point(-180, -90),
        ])
        with arcpy.da.InsertCursor(clip_fc, ["SHAPE@"]) as cur:
            cur.insertRow([arcpy.Polygon(ring, arcpy.SpatialReference(4326))])

        output = self._out("clip_output")
        arcpy.analysis.Clip(input_fc, clip_fc, output)
        return arcpy.Exists(output)


# Intersect the countries layer with a 30-degree global fishnet grid.
# Measures overlay performance across all grid cells and country boundaries.
class IntersectTest(BenchmarkTest):

    def __init__(self, iterations: int = 5):
        super().__init__("Vector Intersect (30° grid)", iterations)
        self._outputs = ["fishnet_grid", "intersect_output"]

    def _execute(self, input_fc: str) -> bool:
        grid = self._out("fishnet_grid")
        # 30° cells produce 12×6 = 72 rectangles covering the globe.
        arcpy.management.CreateFishnet(
            grid,
            origin_coord="-180 -90",
            y_axis_coord="-180 -80",
            cell_width=30, cell_height=30,
            number_rows=None, number_columns=None,
            corner_coord="180 90",
            labels="NO_LABELS",
            template="-180 -90 180 90",
            geometry_type="POLYGON"
        )
        output = self._out("intersect_output")
        arcpy.analysis.Intersect([input_fc, grid], output)
        return arcpy.Exists(output)


# Dissolve states/provinces into country polygons by the adm0_a3 country code.
# Tests the topology-building and geometry merge performance on ~4,600 features.
class DissolveTest(BenchmarkTest):

    def __init__(self, iterations: int = 5):
        super().__init__("Vector Dissolve (states → countries)", iterations)
        self._outputs = ["dissolve_output"]

    def _execute(self, input_fc: str) -> bool:
        output = self._out("dissolve_output")
        arcpy.management.Dissolve(input_fc, output, "adm0_a3")
        return arcpy.Exists(output)


# Spatial join of states/provinces (target) onto countries (join).
# Transfers country attributes to each state using a spatial containment test.
class SpatialJoinTest(BenchmarkTest):

    def __init__(self, iterations: int = 5):
        super().__init__("Vector Spatial Join (states → countries)", iterations)
        self._outputs = ["spatial_join_output"]

    def _execute(self, target_fc: str, join_fc: str) -> bool:
        output = self._out("spatial_join_output")
        arcpy.analysis.SpatialJoin(target_fc, join_fc, output)
        return arcpy.Exists(output)


# Union of states/provinces with a 30-degree global fishnet grid.
# Exercises polygon overlay on a higher-feature-count dataset than IntersectTest.
class UnionTest(BenchmarkTest):

    def __init__(self, iterations: int = 5):
        super().__init__("Vector Union (30° grid)", iterations)
        self._outputs = ["fishnet_grid", "union_output"]

    def _execute(self, input_fc: str) -> bool:
        grid = self._out("fishnet_grid")
        arcpy.management.CreateFishnet(
            grid,
            origin_coord="-180 -90",
            y_axis_coord="-180 -80",
            cell_width=30, cell_height=30,
            number_rows=None, number_columns=None,
            corner_coord="180 90",
            labels="NO_LABELS",
            template="-180 -90 180 90",
            geometry_type="POLYGON"
        )
        output = self._out("union_output")
        arcpy.analysis.Union([input_fc, grid], output)
        return arcpy.Exists(output)


# Erase the Eastern Hemisphere from the states/provinces layer.
# Tests erase performance — geometrically similar to Clip but retains the
# inverse selection, so it exercises the same overlay kernel differently.
class EraseTest(BenchmarkTest):

    def __init__(self, iterations: int = 5):
        super().__init__("Vector Erase (Eastern Hemisphere)", iterations)
        self._outputs = ["erase_boundary", "erase_output"]

    def _execute(self, input_fc: str) -> bool:
        erase_fc = self._out("erase_boundary")
        # See ClipTest for the os.path.basename() note.
        arcpy.management.CreateFeatureclass(
            self.workspace, os.path.basename(erase_fc), "POLYGON",
            spatial_reference=arcpy.SpatialReference(4326)
        )
        ring = arcpy.Array([
            arcpy.Point(0,   -90), arcpy.Point(180, -90),
            arcpy.Point(180,  90), arcpy.Point(0,    90),
            arcpy.Point(0,   -90),
        ])
        with arcpy.da.InsertCursor(erase_fc, ["SHAPE@"]) as cur:
            cur.insertRow([arcpy.Polygon(ring, arcpy.SpatialReference(4326))])

        output = self._out("erase_output")
        arcpy.analysis.Erase(input_fc, erase_fc, output)
        return arcpy.Exists(output)


# =============================================================================
# Raster Analysis Tests
# =============================================================================
# Each test runs against both the SRTM 30m mosaic and the LiDAR 1m DEM so
# that results can be compared across resolutions on the same hardware.
# The .save() pattern (arcpy.sa.Tool().save(path)) writes directly to
# in_memory rather than using a scratch workspace.

# Slope in degrees — measures gradient magnitude, depends on cell size and
# Z units.  Uses the default z_factor=1 (valid when XY and Z share the same
# linear unit, i.e. projected metres or geographic arc-seconds).
class SlopeTest(BenchmarkTest):

    def __init__(self, iterations: int = 5):
        super().__init__("Raster Slope", iterations)
        self._outputs = ["slope_output"]

    def _execute(self, input_raster: str) -> bool:
        output = self._out("slope_output")
        arcpy.sa.Slope(input_raster).save(output)
        return arcpy.Exists(output)


# Aspect (0–360°) — direction of maximum rate of change in elevation.
# Result depends on gradient direction only, not magnitude, so it is
# independent of cell size and z_factor.
class AspectTest(BenchmarkTest):

    def __init__(self, iterations: int = 5):
        super().__init__("Raster Aspect", iterations)
        self._outputs = ["aspect_output"]

    def _execute(self, input_raster: str) -> bool:
        output = self._out("aspect_output")
        arcpy.sa.Aspect(input_raster).save(output)
        return arcpy.Exists(output)


# Hillshade — simulates illumination from a fixed sun angle (azimuth 315°,
# altitude 45° by default).  Exercises the same gradient kernel as Slope
# but also applies a cosine shading step.
class HillshadeTest(BenchmarkTest):

    def __init__(self, iterations: int = 5):
        super().__init__("Raster Hillshade", iterations)
        self._outputs = ["hillshade_output"]

    def _execute(self, input_raster: str) -> bool:
        output = self._out("hillshade_output")
        arcpy.sa.Hillshade(input_raster).save(output)
        return arcpy.Exists(output)


# Focal mean with a circular neighbourhood — the most CPU-intensive raster
# test in the suite.  Each output cell requires averaging all input cells
# within a circle of radius `neighborhood_size` cells, so runtime scales
# with O(n * r²) where n = cell count and r = radius.
# Default: 50-cell radius → ~7,854 cells averaged per output cell.
class FocalStatisticsTest(BenchmarkTest):

    def __init__(self, iterations: int = 3, neighborhood_size: int = 50):
        super().__init__(
            f"Raster Focal Statistics ({neighborhood_size}-cell radius)",
            iterations
        )
        self.neighborhood_size = neighborhood_size
        self._outputs = ["focal_stats_output"]

    def _execute(self, input_raster: str) -> bool:
        neighborhood = arcpy.sa.NbrCircle(self.neighborhood_size, "CELL")
        output = self._out("focal_stats_output")
        arcpy.sa.FocalStatistics(input_raster, neighborhood, "MEAN").save(output)
        return arcpy.Exists(output)


# =============================================================================
# LiDAR Processing Tests
# =============================================================================

# LAS Dataset → Raster conversion — times the point-cloud binning step that
# converts raw LiDAR returns into a gridded elevation surface.
#
# This is the most CPU-intensive test in the suite (~284s per iteration on a
# 65-tile QL2 dataset).  It exercises the same parallel processing pipeline
# that field GIS staff run when deriving DEMs from new LiDAR acquisitions,
# making it a practical real-world workload for CPU upgrade comparisons.
#
# The test writes directly to in_memory to eliminate disk I/O from the timing.
# Cell size of 1m matches the QL2 standard output resolution.
class LiDARToRasterTest(BenchmarkTest):

    def __init__(self, iterations: int = 3, cell_size: float = 1.0):
        super().__init__(f"LiDAR to Raster ({cell_size}m cell)", iterations)
        self.cell_size = cell_size
        self._outputs = ["lidar_raster_output"]

    def _execute(self, lasd_path: str) -> bool:
        output = self._out("lidar_raster_output")
        arcpy.conversion.LasDatasetToRaster(
            in_las_dataset=lasd_path,
            out_raster=output,
            value_field="ELEVATION",
            interpolation_type="BINNING AVERAGE LINEAR",
            data_type="FLOAT",
            sampling_type="CELLSIZE",
            sampling_value=self.cell_size
        )
        return arcpy.Exists(output)


# =============================================================================
# Benchmark Manager
# =============================================================================

# Orchestrates the full benchmark run: system configuration, dataset loading,
# test execution, result collection, and output.
class BenchmarkManager:

    def __init__(self, base_dir: Path, iterations: int = 5,
                 dataset_version: str = "v1.0"):
        self.base_dir        = base_dir
        self.iterations      = iterations
        self.dataset_version = dataset_version
        self.data_dir        = base_dir / "BenchmarkData"
        self.results_dir     = base_dir / "results"
        self.results_dir.mkdir(exist_ok=True)

        self.profiler        = SystemProfiler()
        self.dataset_manager = DatasetManager(self.data_dir, dataset_version)

        self.results      = {}
        self.start_time   = None
        self.end_time     = None
        # Reserve one core for the OS.  On Intel 12th gen+ hybrid CPUs
        # (P-core / E-core), Windows 11 Thread Director is hybrid-aware and
        # will preferentially schedule the reserved core on an E-core rather
        # than a high-performance P-core.
        self.cores_used   = max(1, (os.cpu_count() or 2) - 1)
        self.test_selection = "all"

    # Top-level entry point.  Runs the requested test categories in order and
    # saves a timestamped JSON result file on completion.
    def run_all_tests(self, test_selection: str = "all") -> Dict:
        self.test_selection = test_selection

        logger.info("\n" + "=" * 60)
        logger.info("ARCGIS PRO PERFORMANCE BENCHMARK")
        logger.info("=" * 60)

        self.start_time = datetime.now()

        self._configure_system_for_benchmark()

        # Required for in_memory outputs — ArcGIS defaults to overwriteOutput=False.
        arcpy.env.overwriteOutput = True

        # Set the number of cores ArcGIS geoprocessing tools may use.
        # See comment on self.cores_used for the n-1 rationale.
        arcpy.env.parallelProcessingFactor = str(self.cores_used)
        logger.info(f"Parallel processing: {self.cores_used} of "
                    f"{os.cpu_count()} cores (n-1)")

        if not self.dataset_manager.setup():
            logger.error("\nDataset setup failed. See instructions above.")
            return self._generate_results()

        # Check out licensed extensions — failures are non-fatal so the run
        # continues with whatever is available.
        for ext in ("Spatial", "3D"):
            try:
                arcpy.CheckOutExtension(ext)
            except Exception as e:
                logger.warning(f"Could not check out {ext} extension: {e}")

        # Copy / mosaic all source datasets into in_memory once before tests
        # begin.  This eliminates disk I/O from every benchmark iteration so
        # that timing reflects CPU performance, not storage speed.
        countries_mem, states_mem, dem_mem, lidar_mem = \
            self._load_inputs_to_memory()

        if test_selection in ("all", "vector"):
            self.results["vector_geoprocessing"] = \
                self._run_vector_tests(countries_mem, states_mem)

        if test_selection in ("all", "raster"):
            self.results["raster_analysis"] = \
                self._run_raster_tests(dem_mem)
            if lidar_mem:
                self.results["raster_analysis_lidar_1m"] = \
                    self._run_raster_tests(lidar_mem, label="LiDAR 1m")
            lasd = self.dataset_manager.get_las_dataset()
            if lasd:
                self.results["lidar_processing"] = \
                    self._run_lidar_processing_tests(lasd)

        self.end_time = datetime.now()

        final_results = self._generate_results()
        self._save_results(final_results)
        self._print_summary(final_results)
        self._restore_system_after_benchmark()

        return final_results

    # Raise process priority and switch to the High Performance power plan so
    # that CPU frequency does not throttle during the run, particularly on
    # laptops that default to Balanced / Eco mode.
    def _configure_system_for_benchmark(self):
        import ctypes, subprocess

        try:
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            # HIGH_PRIORITY_CLASS (0x80) gives ArcGIS geoprocessing threads
            # scheduling preference over background applications.
            HIGH_PRIORITY_CLASS = 0x00000080
            ctypes.windll.kernel32.SetPriorityClass(handle, HIGH_PRIORITY_CLASS)
            logger.info("Process priority set to High")
        except Exception as e:
            logger.warning(f"Could not set process priority: {e}")

        try:
            # GUID 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c = High Performance.
            # This plan keeps the CPU at maximum frequency throughout the run,
            # preventing the timing variance caused by frequency scaling.
            result = subprocess.run(
                ['powercfg', '/setactive',
                 '8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c'],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                logger.info("Power plan set to High Performance")
            else:
                logger.warning("Could not set High Performance power plan "
                               "(may not exist on this system)")
        except Exception as e:
            logger.warning(f"Could not set power plan: {e}")

    # Restore the Balanced power plan after the benchmark to return the system
    # to its normal operating mode (important on battery-powered laptops).
    def _restore_system_after_benchmark(self):
        import subprocess
        try:
            # GUID 381b4222-f694-41f0-9685-ff5bb260df2e = Balanced (Windows default).
            subprocess.run(
                ['powercfg', '/setactive',
                 '381b4222-f694-41f0-9685-ff5bb260df2e'],
                capture_output=True
            )
            logger.info("Power plan restored to Balanced")
        except Exception:
            pass

    # Copy vector feature classes and mosaic raster tiles into in_memory so
    # that benchmark iterations read from RAM, not from disk.
    # Returns a 4-tuple: (countries_mem, states_mem, dem_mem, lidar_mem).
    # Any element may be None if the corresponding source data is unavailable.
    def _load_inputs_to_memory(self) -> Tuple[
            Optional[str], Optional[str], Optional[str], Optional[str]]:
        logger.info("\nLoading inputs to in_memory workspace...")

        countries_disk = self.dataset_manager.get_vector_data(
            "ne_10m_admin_0_countries.shp")
        states_disk    = self.dataset_manager.get_vector_data(
            "ne_10m_admin_1_states_provinces.shp")
        raster_tiles   = self.dataset_manager.get_raster_tiles()
        lidar_disk     = self.dataset_manager.get_lidar_dem()

        countries_mem = os.path.join("in_memory", "countries")
        states_mem    = os.path.join("in_memory", "states")
        dem_mem       = os.path.join("in_memory", "dem")

        if countries_disk and arcpy.Exists(countries_disk):
            arcpy.management.CopyFeatures(countries_disk, countries_mem)
            logger.info(f"  Vector loaded: {os.path.basename(countries_disk)}")
        else:
            logger.warning("  Countries data not found, some tests will be skipped")
            countries_mem = None

        if states_disk and arcpy.Exists(states_disk):
            arcpy.management.CopyFeatures(states_disk, states_mem)
            logger.info(f"  Vector loaded: {os.path.basename(states_disk)}")
        else:
            logger.info("  States/provinces data not found, skipping related tests")
            states_mem = None

        if raster_tiles:
            if len(raster_tiles) == 1:
                # Single tile — copy directly without the mosaic overhead.
                arcpy.management.CopyRaster(raster_tiles[0], dem_mem)
                logger.info(f"  Raster loaded: {os.path.basename(raster_tiles[0])}")
            else:
                # Multiple SRTM tiles — mosaic into a single in_memory raster
                # so all raster tests see a seamless dataset.
                logger.info(f"  Mosaicking {len(raster_tiles)} raster tiles...")
                arcpy.management.MosaicToNewRaster(
                    input_rasters=raster_tiles,
                    output_location="in_memory",
                    raster_dataset_name_with_extension="dem",
                    pixel_type="32_BIT_FLOAT",
                    number_of_bands=1,
                    mosaic_method="LAST"
                )
                logger.info(f"  Mosaic complete: {len(raster_tiles)} tiles")
        else:
            logger.warning("  No raster tiles found, raster tests will be skipped")
            dem_mem = None

        # LiDAR DEM is loaded separately — it is used for raster_analysis_lidar_1m
        # tests alongside (not instead of) the SRTM mosaic.
        lidar_mem = None
        if lidar_disk and arcpy.Exists(lidar_disk):
            lidar_mem = os.path.join("in_memory", "lidar_dem")
            arcpy.management.CopyRaster(lidar_disk, lidar_mem)
            logger.info(f"  LiDAR DEM loaded: {os.path.basename(lidar_disk)}")

        return countries_mem, states_mem, dem_mem, lidar_mem

    # Run all vector geoprocessing tests.
    # Countries tests (~250 features) and states tests (~4,600 features) run
    # separately so the result JSON distinguishes performance by feature count.
    def _run_vector_tests(self, countries: Optional[str],
                          states: Optional[str]) -> Dict:
        logger.info("\n" + "=" * 60)
        logger.info("VECTOR GEOPROCESSING TESTS")
        logger.info("=" * 60)

        results = {}

        if countries and arcpy.Exists(countries):
            results["buffer_100m_countries"]   = BufferTest(self.iterations).run(countries)
            results["clip_western_hemisphere"]  = ClipTest(self.iterations).run(countries)
            results["intersect_30deg_grid"]     = IntersectTest(self.iterations).run(countries)
        else:
            logger.warning("Countries data not available, skipping countries tests")

        if states and arcpy.Exists(states):
            results["buffer_100m_states"]            = BufferTest(self.iterations).run(states)
            results["dissolve_states_to_countries"]  = DissolveTest(self.iterations).run(states)
            results["spatial_join_states_countries"] = SpatialJoinTest(self.iterations).run(
                states, countries)
            results["union_30deg_grid"]              = UnionTest(self.iterations).run(states)
            results["erase_eastern_hemisphere"]      = EraseTest(self.iterations).run(states)
        else:
            logger.info("States/provinces data not available, skipping states tests")

        return results

    # Run Slope, Aspect, Hillshade, and FocalStatistics on the supplied DEM.
    # Called twice per full run — once for the SRTM mosaic and once for the
    # LiDAR 1m DEM — to allow direct resolution comparison.
    def _run_raster_tests(self, dem: Optional[str],
                          label: str = "SRTM 30m") -> Dict:
        logger.info("\n" + "=" * 60)
        logger.info(f"RASTER ANALYSIS TESTS - {label}")
        logger.info("=" * 60)

        results = {}
        if dem and arcpy.Exists(dem):
            # Log raster dimensions and CRS to confirm the correct dataset was
            # loaded and to record cell count context alongside the timing results.
            try:
                r  = arcpy.Raster(dem)
                sr = arcpy.Describe(dem).spatialReference
                logger.info(
                    f"  Input raster: {r.width}×{r.height} cells, "
                    f"cell {r.meanCellWidth:.2f}×{r.meanCellHeight:.2f}, "
                    f"CRS: {sr.name}"
                )
            except Exception:
                pass

            results["slope"]                  = SlopeTest(self.iterations).run(dem)
            results["aspect"]                 = AspectTest(self.iterations).run(dem)
            results["hillshade"]              = HillshadeTest(self.iterations).run(dem)
            # FocalStatistics uses 3 iterations (not 5) because each run takes
            # ~25s; 3 iterations still yields a reliable mean while keeping
            # total raster test time reasonable.
            results["focal_statistics_50cell"] = FocalStatisticsTest(
                iterations=3, neighborhood_size=50
            ).run(dem)
        else:
            logger.warning("Raster data not available, skipping raster tests")

        return results

    # Run LiDAR point-cloud-to-raster conversion benchmark.
    # Uses 3 iterations — each iteration takes ~284s on the current 65-tile
    # dataset, so 3 iterations = ~14 minutes for this category alone.
    def _run_lidar_processing_tests(self, lasd: str) -> Dict:
        logger.info("\n" + "=" * 60)
        logger.info("LIDAR PROCESSING TESTS")
        logger.info("=" * 60)

        results = {}
        if lasd and arcpy.Exists(lasd):
            results["las_to_raster_1m"] = LiDARToRasterTest(
                iterations=3, cell_size=1.0
            ).run(lasd)
        else:
            logger.warning("LAS Dataset not available, skipping LiDAR tests")

        return results

    # Assemble the final results dict that is written to JSON and printed.
    def _generate_results(self) -> Dict:
        total_time = (
            (self.end_time - self.start_time).total_seconds()
            if self.start_time and self.end_time else 0
        )
        return {
            "benchmark_id":        self.start_time.strftime("%Y%m%d_%H%M%S")
                                   if self.start_time else "unknown",
            "timestamp":           self.start_time.isoformat()
                                   if self.start_time else datetime.now().isoformat(),
            "dataset_version":     self.dataset_version,
            "iterations":          self.iterations,
            "cores_used":          self.cores_used,
            "test_selection":      self.test_selection,
            "system_info":         self.profiler.profile,
            "results":             self.results,
            "total_time_seconds":  round(total_time, 2),
        }

    # Write results to a timestamped JSON file in the results/ folder.
    # The file is named benchmark_YYYYMMDD_HHMMSS.json so that multiple runs
    # sort chronologically and are unambiguous when committed to the repository.
    def _save_results(self, results: Dict):
        filename = f"benchmark_{results['benchmark_id']}.json"
        filepath = self.results_dir / filename
        with open(filepath, 'w') as f:
            json.dump(results, f, indent=2)
        logger.info(f"\nResults saved to: {filepath}")

    # Print a compact summary to the console at the end of the run.
    def _print_summary(self, results: Dict):
        logger.info("\n" + "=" * 60)
        logger.info("BENCHMARK SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Total time: {results['total_time_seconds']}s")
        logger.info(f"System: {results['system_info']['cpu_model']}")
        logger.info(
            f"ArcGIS Pro: {results['system_info']['arcgis_version']} "
            f"(build {results['system_info']['arcgis_build']})"
        )
        logger.info(f"Cores used: {results['cores_used']} of "
                    f"{results['system_info']['cpu_cores']}")
        logger.info(f"Iterations per test: {results['iterations']}")
        logger.info("\nTest Results (mean times):")

        for category, tests in results['results'].items():
            if tests:
                logger.info(f"\n  {category}:")
                for test_name, test_result in tests.items():
                    if test_result.get('status') == 'success':
                        logger.info(
                            f"    {test_name}: {test_result['mean']}s "
                            f"± {test_result['std_dev']}s"
                        )
                    else:
                        logger.info(f"    {test_name}: FAILED")


# =============================================================================
# Entry Point
# =============================================================================

# Interactive prompt displayed when the script is run directly without the
# --tests argument (e.g. from IDLE or double-clicked in Explorer).
def _prompt_test_selection() -> str:
    print("\nSelect tests to run:")
    print("  1. All (vector + raster)  [default]")
    print("  2. Vector only")
    print("  3. Raster only")
    choice = input("Enter choice [1-3, or press Enter for default]: ").strip()
    return {'1': 'all', '2': 'vector', '3': 'raster'}.get(choice, 'all')


def main():
    # Paths and global run settings — edit here to customise the benchmark.
    BASE_DIR        = Path(__file__).parent
    ITERATIONS      = 5      # trials per test (except FocalStatistics and LiDAR = 3)
    DATASET_VERSION = "v1.0"

    parser = argparse.ArgumentParser(description="ArcGIS Pro Performance Benchmark")
    parser.add_argument(
        '--tests', choices=['all', 'vector', 'raster'],
        help='Test categories to run (omit for interactive prompt)'
    )
    args = parser.parse_args()

    # Use the CLI argument if provided; otherwise show the interactive prompt.
    test_selection = args.tests if args.tests else _prompt_test_selection()

    try:
        manager = BenchmarkManager(
            base_dir=BASE_DIR,
            iterations=ITERATIONS,
            dataset_version=DATASET_VERSION
        )
        manager.run_all_tests(test_selection=test_selection)
        logger.info("\nBenchmark completed successfully!")

    except Exception as e:
        logger.error(f"Benchmark failed: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
