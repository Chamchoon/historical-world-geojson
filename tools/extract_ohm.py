#!/usr/bin/env python3
"""
OpenHistoricalMap (OHM) GeoJSON Extractor

Extracts historical world/regional country borders from OpenHistoricalMap (OHM)
for any given year or date, reconstructing valid polygon geometries and formatting
attributes to match the Historical Maps GeoJSON repository standard.

Usage:
  python tools/extract_ohm.py --year 1914 --output maps/standard/world_1914.geojson
  python tools/extract_ohm.py --year 1914 --bbox 35,-15,70,40 --output maps/standard/europe_1914.geojson
"""

import sys
import re
import argparse
import requests
import pandas as pd
import geopandas as gpd
from shapely.geometry import LineString, Polygon, MultiPolygon, Point
from shapely.ops import linemerge, polygonize, unary_union
from shapely import make_valid

OHM_OVERPASS_URL = "https://overpass-api.openhistoricalmap.org/api/interpreter"


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

    # Merge connected outer segments
    merged_outer = linemerge(outer_lines)
    outer_polys = list(polygonize(merged_outer))

    # If simple linemerge did not close all segments, try snapping endpoints
    if not outer_polys and isinstance(merged_outer, (MultiPolygon, LineString)):
        # Attempt polygonize directly on all line segments
        outer_polys = list(polygonize(outer_lines))

    if not outer_polys:
        return None, capital_info

    # Union all outer rings (creates Polygon or MultiPolygon)
    poly_geom = unary_union(outer_polys)

    # Cut out inner holes (lakes, enclaves)
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

    # Ensure validity
    if not poly_geom.is_valid:
        poly_geom = make_valid(poly_geom)

    # Keep only Polygon / MultiPolygon components
    if poly_geom.geom_type == "GeometryCollection":
        parts = [g for g in poly_geom.geoms if g.geom_type in ["Polygon", "MultiPolygon"] and not g.is_empty]
        if not parts:
            return None, capital_info
        poly_geom = unary_union(parts)

    if poly_geom.is_empty or poly_geom.geom_type not in ["Polygon", "MultiPolygon"]:
        return None, capital_info

    return poly_geom, capital_info


def query_ohm(target_year=None, target_date=None, bbox=None, admin_level=2, timeout=120):
    """
    Builds and executes an Overpass QL query against OpenHistoricalMap.
    """
    if target_date:
        start_filter = f't["start_date"] <= "{target_date}"'
        end_filter = f'(!is_tag("end_date") || t["end_date"] >= "{target_date}")'
    elif target_year:
        start_filter = f't["start_date"] <= "{target_year}-12-31"'
        end_filter = f'(!is_tag("end_date") || t["end_date"] >= "{target_year}-01-01")'
    else:
        raise ValueError("Must specify either target_year or target_date")

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
    print(f"    Date / Year: {target_date or target_year}")
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
    """
    Parses OHM relations into a GeoPandas GeoDataFrame with standard repo attributes.
    """
    records = []
    print("[*] Reconstructing polygons and extracting metadata...")

    for el in elements:
        tags = el.get("tags", {})
        members = el.get("members", [])
        ohm_id = el.get("id")

        geom, capital_info = polygonize_relation(members)
        if geom is None:
            continue

        # Preferred name
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

    # Calculate territory area in km² (Equal Earth projection EPSG:8857)
    print("[*] Calculating territory areas (km²)...")
    try:
        areas_km2 = gdf.to_crs("EPSG:8857").area / 1e6
        gdf["area"] = areas_km2.round(1)
    except Exception as e:
        print(f"    Warning: Area calculation fallback due to: {e}")
        gdf["area"] = 0.0

    # Sort alphabetically by country name
    gdf = gdf.sort_values(by="cntry_name").reset_index(drop=True)
    return gdf


def main():
    parser = argparse.ArgumentParser(
        description="Extract historical country borders from OpenHistoricalMap into standardized GeoJSON."
    )
    parser.add_argument("--year", type=int, help="Historical year (e.g., 1914, 1890, 1939)")
    parser.add_argument("--date", type=str, help="Specific ISO date (e.g., 1914-06-28)")
    parser.add_argument(
        "--bbox",
        type=str,
        default=None,
        help="Optional bounding box: south,west,north,east (e.g., 35,-15,70,40)"
    )
    parser.add_argument(
        "--admin-level",
        type=int,
        default=2,
        help="Administrative boundary level (default: 2 for sovereign countries / colonies)"
    )
    parser.add_argument(
        "--output",
        type=str,
        required=True,
        help="Output GeoJSON filepath (e.g., maps/standard/world_1914.geojson)"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="Overpass query timeout in seconds (default: 120)"
    )

    args = parser.parse_args()

    if not args.year and not args.date:
        parser.error("You must specify either --year or --date.")

    elements = query_ohm(
        target_year=args.year,
        target_date=args.date,
        bbox=args.bbox,
        admin_level=args.admin_level,
        timeout=args.timeout
    )

    gdf = extract_to_geodataframe(elements)
    if gdf.empty:
        print("[-] Exiting: No data extracted.")
        sys.exit(1)

    print(f"[+] Successfully extracted {len(gdf)} entities.")
    print(f"[*] Writing to {args.output}...")
    
    # Save as standard RFC 7946 GeoJSON
    gdf.to_file(args.output, driver="GeoJSON")
    print(f"[✓] Saved {args.output} successfully!")


if __name__ == "__main__":
    main()
