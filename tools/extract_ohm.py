#!/usr/bin/env python3
"""
OpenHistoricalMap (OHM) Extractor & Coastline Harmonizer

Extracts historical country borders from OpenHistoricalMap and reconstructs
geometries using the Coastal Slivers Snapper method (Natural Earth coastlines),
completely replacing raw, bubbly crowdsourced coastlines with crisp cartographic
coastlines.

Generates three standardized tiers:
  1. High-Detail (~22.5 MB): Full Natural Earth 10m fidelity for 4K video & GIS.
  2. Standard (4.5 MB): Topology-preserving simplified coastlines (tolerance ~0.024)
     optimized for web maps (Leaflet, Mapbox, D3.js).
  3. Low (<2 MB): Ultra-compact, 4-decimal precision (tolerance ~0.05) for mobile.

Usage Examples:
  # Query OHM and generate ALL THREE tiers in one command:
  python tools/extract_ohm.py --year 1914 \
    --output-high maps/high-detail/world_1914.geojson \
    --output-standard maps/standard/world_1914.geojson \
    --output-low maps/low/world_1914.geojson

  # Process an already-downloaded raw extract:
  python tools/extract_ohm.py --input maps/raw_1914.geojson \
    --output-high maps/high-detail/world_1914.geojson \
    --output-standard maps/standard/world_1914.geojson \
    --output-low maps/low/world_1914.geojson
"""

import os
import sys
import re
import argparse
import warnings
import requests
import pandas as pd
import geopandas as gpd
from shapely.geometry import LineString, Polygon, MultiPolygon
from shapely.ops import linemerge, polygonize, unary_union
from shapely import make_valid

# Import coastline upscaler functions from current directory
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from upscale_coastlines import enhance_coastlines_with_slivers, sanitize_polygons

OHM_OVERPASS_URL = "https://overpass-api.openhistoricalmap.org/api/interpreter"

# Default candidate locations for reference land data (Natural Earth 10m)
DEFAULT_REFERENCE_PATHS = [
    os.path.join(os.path.dirname(__file__), "reference_data", "ne_10m_land.shp"),
    "/home/hamza/Desktop/hamza/AutoPulse/ContentCreation/image/statBot/videoFunfacts/mapsAnimations/reference_data/ne_10m_land.shp",
    "ne_10m_land.shp",
    "ne_10m_land.geojson",
]


def find_default_reference():
    """Finds the first existing reference land dataset from standard locations."""
    for path in DEFAULT_REFERENCE_PATHS:
        if os.path.exists(path):
            return path
    return None


def parse_year(date_str):
    """Extracts a 4-digit year integer from an OHM date string if possible."""
    if not date_str:
        return None
    match = re.search(r"\b(\d{3,4})\b", str(date_str))
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None
    return None


def polygonize_relation(members):
    """
    Reconstructs Polygon or MultiPolygon from OHM relation member ways.
    Handles outer rings, inner holes, and MultiPolygons.
    """
    outer_lines = []
    inner_lines = []
    capital_info = None

    for m in members:
        # Check for capital / admin_centre node
        if m.get("type") == "node" and m.get("role") in ["admin_centre", "capital", "label"]:
            lat = m.get("lat")
            lon = m.get("lon")
            node_tags = m.get("tags", {})
            capname = node_tags.get("name:en") or node_tags.get("name")
            if lat is not None and lon is not None:
                capital_info = {"capname": capname, "caplat": float(lat), "caplong": float(lon)}

        # Way geometry
        if m.get("type") == "way" and "geometry" in m and len(m["geometry"]) >= 2:
            coords = [(pt["lon"], pt["lat"]) for pt in m["geometry"]]
            ls = LineString(coords)
            if m.get("role") == "inner":
                inner_lines.append(ls)
            else:
                outer_lines.append(ls)

    if not outer_lines:
        return None, capital_info

    merged_outer = linemerge(outer_lines)
    outer_polys = list(polygonize(merged_outer))

    if not outer_polys and isinstance(merged_outer, (MultiPolygon, LineString)):
        outer_polys = list(polygonize(outer_lines))

    if not outer_polys:
        return None, capital_info

    poly_geom = unary_union(outer_polys)

    if inner_lines:
        merged_inner = linemerge(inner_lines)
        inner_polys = list(polygonize(merged_inner))
        if not inner_polys:
            inner_polys = list(polygonize(inner_lines))
        if inner_polys:
            inner_geom = unary_union(inner_polys)
            try:
                poly_geom = poly_geom.difference(inner_geom)
            except Exception:
                pass

    if not poly_geom.is_valid:
        poly_geom = make_valid(poly_geom)

    if poly_geom.geom_type == "GeometryCollection":
        parts = [g for g in poly_geom.geoms if g.geom_type in ["Polygon", "MultiPolygon"] and not g.is_empty]
        if not parts:
            return None, capital_info
        poly_geom = unary_union(parts)

    if poly_geom.is_empty or poly_geom.geom_type not in ["Polygon", "MultiPolygon"]:
        return None, capital_info

    return poly_geom, capital_info


def query_ohm(target_year=None, target_date=None, bbox=None, admin_level=2, timeout=180):
    """
    Builds and executes an Overpass QL query against OpenHistoricalMap.
    Anchors to mid-year (July 1st) when a whole year is requested to prevent intra-year duplicates.
    """
    if target_date:
        query_date = target_date
    elif target_year:
        query_date = f"{target_year:04d}-07-01"
    else:
        raise ValueError("Must specify either target_year or target_date")

    start_filter = f't["start_date"] <= "{query_date}"'
    end_filter = f'(!is_tag("end_date") || t["end_date"] >= "{query_date}")'
    bbox_filter = f"({bbox})" if bbox else ""

    ql_query = f"""
    [out:json][timeout:{timeout}];
    relation
      ["boundary"="administrative"]
      ["admin_level"="{admin_level}"]
      {bbox_filter}
      (if: (is_tag("start_date") && {start_filter}) && {end_filter});
    out geom;
    """

    print(f"[*] Querying OpenHistoricalMap Overpass API...")
    if bbox:
        print(f"    Bounding Box: {bbox}")
    print(f"    Target Snapshot: {query_date}")
    print(f"    Admin Level: {admin_level}")

    resp = requests.post(
        OHM_OVERPASS_URL,
        data={"data": ql_query},
        headers={"User-Agent": "HistoricalMapsGeoJSONExtractor/1.0"},
        timeout=timeout + 30
    )
    resp.raise_for_status()
    data = resp.json()
    elements = data.get("elements", [])
    print(f"[+] Received {len(elements)} relation elements from OHM.")
    return elements


def extract_to_geodataframe(elements):
    """Parses raw OHM relations into a GeoDataFrame with standard repo attributes."""
    records = []
    print("[*] Reconstructing polygons and extracting metadata...")

    for el in elements:
        tags = el.get("tags", {})
        members = el.get("members", [])
        ohm_id = el.get("id")

        geom, capital_info = polygonize_relation(members)
        if geom is None:
            continue

        cntry_name = (
            tags.get("name:en")
            or tags.get("name")
            or tags.get("int_name")
            or tags.get("short_name")
            or f"Territory {ohm_id}"
        )

        start_date = tags.get("start_date")
        end_date = tags.get("end_date")

        capname = capital_info["capname"] if capital_info else tags.get("capital")
        caplat = capital_info["caplat"] if capital_info else None
        caplong = capital_info["caplong"] if capital_info else None

        records.append({
            "cntry_name": cntry_name,
            "native_name": tags.get("name"),
            "geometry": geom,
            "start_date": start_date,
            "end_date": end_date,
            "start_year": parse_year(start_date),
            "end_year": parse_year(end_date),
            "capname": capname,
            "caplat": caplat,
            "caplong": caplong,
            "wikidata": tags.get("wikidata"),
            "ohm_id": f"relation/{ohm_id}"
        })

    if not records:
        print("[-] No valid polygon geometries could be formed.")
        return gpd.GeoDataFrame()

    gdf = gpd.GeoDataFrame(records, crs="EPSG:4326")

    # Calculate territory area in km²
    print("[*] Calculating territory areas (km²)...")
    try:
        areas_km2 = gdf.to_crs("EPSG:8857").area / 1e6
        gdf["area"] = areas_km2.round(1)
    except Exception as e:
        print(f"    Warning: Area calculation fallback due to: {e}")
        gdf["area"] = 0.0

    # Deduplicate in case multiple relations share the exact same country name
    before_count = len(gdf)
    gdf = gdf.sort_values(by=["cntry_name", "area"], ascending=[True, False]).drop_duplicates(
        subset=["cntry_name"], keep="first"
    )
    if len(gdf) < before_count:
        print(f"[*] Deduplicated {before_count - len(gdf)} overlapping entity versions.")

    return gdf.sort_values(by="cntry_name").reset_index(drop=True)


def process_map_pipeline(
    raw_gdf,
    ref_land_path,
    output_high=None,
    output_standard=None,
    output_low=None,
    high_tolerance=0.001,
    standard_tolerance=0.024,
    low_tolerance=0.05
):
    """
    Applies the Coastal Slivers Snapper algorithm using the reference land data,
    replacing bubbly OHM coastlines and generating high-detail (~23 MB),
    standard web-ready (4-5 MB), and low-bandwidth (< 2 MB) tiers.
    """
    print(f"\n[*] Harmonizing coastlines with reference: {ref_land_path}")
    land_gdf = gpd.read_file(ref_land_path)
    if land_gdf.crs is None:
        land_gdf.set_crs("EPSG:4326", inplace=True)
    if raw_gdf.crs != land_gdf.crs:
        raw_gdf = raw_gdf.to_crs(land_gdf.crs)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        upscaled_gdf = enhance_coastlines_with_slivers(raw_gdf, land_gdf)

    # Recalculate areas based on clean snapped coastlines
    try:
        upscaled_gdf["area"] = (upscaled_gdf.to_crs("EPSG:8857").area / 1e6).round(1)
    except Exception:
        pass

    # Save High-Detail Tier (~22-24 MB)
    if output_high:
        out_dir = os.path.dirname(output_high)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        print(f"\n[*] Generating High-Detail map (preserving sub-kilometer 4K fidelity, tolerance={high_tolerance})...")
        high_gdf = upscaled_gdf.copy()
        if high_tolerance > 0:
            high_gdf["geometry"] = high_gdf.geometry.simplify(
                tolerance=high_tolerance, preserve_topology=True
            )
        print(f"[*] Writing High-Detail map to: {output_high}...")
        high_gdf.to_file(output_high, driver="GeoJSON")
        high_size = os.path.getsize(output_high) / (1024 * 1024)
        print(f"[✓] High-Detail Map Created: {high_size:.2f} MB")

    # Save Standard Tier (4-5 MB)
    if output_standard:
        out_dir = os.path.dirname(output_standard)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        print(f"\n[*] Generating Standard web-ready map (tolerance={standard_tolerance})...")
        std_gdf = upscaled_gdf.copy()
        std_gdf["geometry"] = std_gdf.geometry.simplify(
            tolerance=standard_tolerance, preserve_topology=True
        )
        print(f"[*] Writing Standard map to: {output_standard}...")
        std_gdf.to_file(output_standard, driver="GeoJSON")
        std_size = os.path.getsize(output_standard) / (1024 * 1024)
        print(f"[✓] Standard Map Created: {std_size:.2f} MB")

    # Save Low Tier (< 2 MB)
    if output_low:
        out_dir = os.path.dirname(output_low)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        print(f"\n[*] Generating Low-bandwidth map (< 2 MB, tolerance={low_tolerance})...")
        from shapely.geometry import mapping, shape
        from shapely import make_valid
        low_gdf = upscaled_gdf.copy()
        low_gdf["geometry"] = low_gdf.geometry.simplify(
            tolerance=low_tolerance, preserve_topology=True
        )
        def _round_geom(geom):
            if geom is None or geom.is_empty:
                return geom
            gj = mapping(geom)
            def _r(c):
                if isinstance(c[0], (int, float)):
                    return [round(c[0], 4), round(c[1], 4)]
                return [_r(x) for x in c]
            gj["coordinates"] = _r(gj["coordinates"])
            s = shape(gj)
            if not s.is_valid:
                s = make_valid(s)
            return s
        low_gdf["geometry"] = low_gdf["geometry"].apply(_round_geom)
        print(f"[*] Writing Low-tier map to: {output_low}...")
        low_gdf.to_file(output_low, driver="GeoJSON")
        low_size = os.path.getsize(output_low) / (1024 * 1024)
        print(f"[✓] Low-Tier Map Created: {low_size:.2f} MB")


def main():
    parser = argparse.ArgumentParser(
        description="Extract historical country borders from OHM and snap to crisp reference coastlines."
    )
    # Input options: either live OHM query or local raw GeoJSON
    group_in = parser.add_mutually_exclusive_group(required=True)
    group_in.add_argument("--year", type=int, help="Historical year to query from OHM (e.g. 1914)")
    group_in.add_argument("--date", type=str, help="Specific ISO date to query from OHM (e.g. 1914-06-28)")
    group_in.add_argument("--input", type=str, help="Path to already extracted raw GeoJSON file")

    # Reference land dataset
    parser.add_argument(
        "-r", "--reference",
        type=str,
        default=None,
        help="Path to Natural Earth land dataset (default: auto-detects ne_10m_land.shp)"
    )

    # Output options
    parser.add_argument(
        "--output-high",
        type=str,
        default=None,
        help="Path to output High-Detail (~22.5 MB) GeoJSON (e.g. maps/high-detail/world_1914.geojson)"
    )
    parser.add_argument(
        "--output-standard",
        type=str,
        default=None,
        help="Path to output Standard (4-5 MB) GeoJSON (e.g. maps/standard/world_1914.geojson)"
    )
    parser.add_argument(
        "--output-low",
        type=str,
        default=None,
        help="Path to output Low-bandwidth (<2 MB) GeoJSON (e.g. maps/low/world_1914.geojson)"
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Single output path (used with --tier)"
    )
    parser.add_argument(
        "--tier",
        type=str,
        choices=["high", "standard", "both"],
        default="both",
        help="If --output is specified, which tier to generate (default: both)"
    )

    # Tuning parameters
    parser.add_argument(
        "--high-tolerance",
        type=float,
        default=0.001,
        help="Simplification tolerance for the high-detail tier (default: 0.001 -> ~23 MB, 0 for raw)"
    )
    parser.add_argument(
        "--standard-tolerance",
        type=float,
        default=0.024,
        help="Simplification tolerance for the standard web-ready tier (default: 0.024 -> ~4.5 MB)"
    )
    parser.add_argument(
        "--low-tolerance",
        type=float,
        default=0.05,
        help="Simplification tolerance for the low-bandwidth tier (default: 0.05 -> ~1.5 MB)"
    )
    parser.add_argument(
        "--bbox",
        type=str,
        default=None,
        help="Optional bounding box for OHM query: south,west,north,east"
    )
    parser.add_argument(
        "--admin-level",
        type=int,
        default=2,
        help="Administrative boundary level (default: 2)"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=180,
        help="OHM query timeout in seconds (default: 180)"
    )

    args = parser.parse_args()

    # Determine reference dataset
    ref_path = args.reference or find_default_reference()
    if not ref_path or not os.path.exists(ref_path):
        print("[-] Error: Reference land dataset (e.g. ne_10m_land.shp) not found.")
        print("    Please provide --reference path/to/ne_10m_land.shp")
        sys.exit(1)

    # Resolve output targets
    out_high = args.output_high
    out_std = args.output_standard
    out_low = args.output_low
    if args.output:
        if args.tier == "high":
            out_high = args.output
        elif args.tier == "standard":
            out_std = args.output
        elif args.tier == "both":
            out_std = args.output

    if not out_high and not out_std and not out_low:
        parser.error("You must specify --output-standard, --output-high, --output-low, or --output.")

    # 1. Obtain raw GeoDataFrame (either from file or OHM)
    if args.input:
        print(f"[*] Reading input GeoJSON: {args.input}...")
        raw_gdf = gpd.read_file(args.input)
    else:
        elements = query_ohm(
            target_year=args.year,
            target_date=args.date,
            bbox=args.bbox,
            admin_level=args.admin_level,
            timeout=args.timeout
        )
        raw_gdf = extract_to_geodataframe(elements)

    if raw_gdf.empty:
        print("[-] No features available to process.")
        sys.exit(1)

    # 2. Run Coastal Slivers Snapping and Tier Generation Pipeline
    process_map_pipeline(
        raw_gdf=raw_gdf,
        ref_land_path=ref_path,
        output_high=out_high,
        output_standard=out_std,
        output_low=out_low,
        high_tolerance=args.high_tolerance,
        standard_tolerance=args.standard_tolerance,
        low_tolerance=args.low_tolerance
    )

    print("\n==========================================")
    print("✨ Pipeline Complete! Crisp historical borders generated.")
    print("==========================================")


if __name__ == "__main__":
    main()
