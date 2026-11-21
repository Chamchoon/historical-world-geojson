#!/usr/bin/env python3
"""
Historical Map Coastline Upscaler (Coastal Slivers Snapper)

Snaps low-resolution historical map boundaries to modern, high-resolution 
coastlines (such as Natural Earth 10m) without distorting historical inland borders.

How it works:
1. Calculates unified modern high-resolution land footprint.
2. Identifies missing coastal slivers and islands (difference between modern land and historical map).
3. Snaps each coastal sliver to the nearest historical country (sjoin_nearest) with deduplication.
4. Dissolves slivers into historical country geometries.
5. Sanitizes degenerate geometries and outputs clean, high-fidelity GeoJSON.
"""

import os
import sys
import argparse
import warnings
import pandas as pd
import geopandas as gpd
from shapely.ops import unary_union
from shapely.geometry import Polygon, MultiPolygon


def sanitize_polygons(geom):
    """Ensures geometries are strictly Polygon or MultiPolygon, removing LineStrings/Points."""
    if geom is None or geom.is_empty:
        return None
    if geom.geom_type in ['Polygon', 'MultiPolygon']:
        return geom
    if geom.geom_type == 'GeometryCollection':
        polys = [
            g for g in geom.geoms 
            if g.geom_type in ['Polygon', 'MultiPolygon'] and not g.is_empty and g.area > 1e-7
        ]
        if not polys:
            return None
        return unary_union(polys)
    return None


def clean_target_geom(geom):
    """Cleans degenerate artifacts and zero-area geometries."""
    if geom is None or geom.is_empty:
        return None
    if geom.geom_type == 'Polygon':
        return geom if geom.area > 1e-7 else None
    elif geom.geom_type == 'MultiPolygon':
        valid = [p for p in geom.geoms if not p.is_empty and p.area > 1e-7]
        if not valid:
            return None
        return MultiPolygon(valid)
    elif geom.geom_type == 'GeometryCollection':
        valid = [
            p for p in geom.geoms 
            if p.geom_type in ['Polygon', 'MultiPolygon'] and not p.is_empty and p.area > 1e-7
        ]
        if not valid:
            return None
        return unary_union(valid)
    return None


def enhance_coastlines_with_slivers(target_gdf, land_gdf):
    """Snaps low-res polygons to high-res coastlines without overlapping inland borders."""
    print("  -> Cleaning input geometries...")
    target_gdf['geometry'] = target_gdf.geometry.apply(clean_target_geom)
    target_gdf = target_gdf[target_gdf.geometry.notnull() & ~target_gdf.geometry.is_empty].reset_index(drop=True)
    target_gdf['geometry'] = target_gdf.geometry.buffer(0)

    # Assign unique tracking ID to each territory
    target_gdf['_country_id'] = range(len(target_gdf))
    
    # 1. Dissolve reference land into a single unified land footprint
    print("  -> Calculating unified reference land footprint...")
    try:
        ref_land_union = land_gdf.geometry.buffer(0).union_all()
    except AttributeError:
        ref_land_union = land_gdf.geometry.buffer(0).unary_union
    ref_land_gdf = gpd.GeoDataFrame(geometry=[ref_land_union], crs=target_gdf.crs)

    # 2. Dissolve the historical map into a single footprint
    print("  -> Calculating historical footprint...")
    hist_land = target_gdf.dissolve()
    
    # 3. Find missing coastal slivers against the reference land
    print("  -> Identifying missing coastal slivers (difference)...")
    missing_land = gpd.overlay(ref_land_gdf, hist_land, how='difference')
    
    # Explode into individual island / coastal sliver polygons
    print("  -> Processing individual slivers...")
    missing_slivers = missing_land.explode(index_parts=False).reset_index(drop=True)
    missing_slivers = missing_slivers[missing_slivers.geometry.area > 1e-7].reset_index(drop=True)
    
    # 4. Assign each sliver to the nearest historical country
    print("  -> Snapping slivers to nearest countries (sjoin_nearest)...")
    slivers_with_country = gpd.sjoin_nearest(missing_slivers, target_gdf, how='left')
    
    # Deduplicate slivers so no sliver belongs to multiple countries
    slivers_with_country = slivers_with_country[~slivers_with_country.index.duplicated(keep='first')]

    cols_to_keep = list(target_gdf.columns)
    slivers_clean = slivers_with_country[cols_to_keep]
    
    # 5. Merge slivers with original countries
    print("  -> Merging geometry...")
    combined = pd.concat([target_gdf, slivers_clean])
    
    # Dissolve by unique country ID so internal historical borders are strictly preserved
    combined_dissolved = combined.dissolve(by='_country_id').reset_index(drop=True)
    
    if '_country_id' in combined_dissolved.columns:
        combined_dissolved = combined_dissolved.drop(columns=['_country_id'])
    
    # 6. Final trim against reference coastlines
    print("  -> Performing final trim against reference coastlines...")
    final_gdf = gpd.clip(combined_dissolved, ref_land_gdf)
    
    # 7. Final geometry sanitization
    final_gdf['geometry'] = final_gdf.geometry.apply(sanitize_polygons)
    final_gdf = final_gdf[final_gdf.geometry.notnull() & ~final_gdf.geometry.is_empty].reset_index(drop=True)

    return final_gdf


def upscale_map(input_file, output_file, reference_file):
    print(f"\n==========================================")
    print(f"🗺️  Historical Map Coastline Upscaler")
    print(f"==========================================")
    print(f"Input:     {input_file}")
    print(f"Reference: {reference_file}")
    print(f"Output:    {output_file}")

    if not os.path.exists(input_file):
        print(f"❌ Error: Input file not found: {input_file}")
        sys.exit(1)
        
    if not os.path.exists(reference_file):
        print(f"❌ Error: Reference file not found: {reference_file}")
        sys.exit(1)

    print("\nLoading datasets...")
    target_gdf = gpd.read_file(input_file)
    land_gdf = gpd.read_file(reference_file)
    print(f"  Target features:    {len(target_gdf)}")
    print(f"  Reference features: {len(land_gdf)}")

    # Ensure CRS matches
    if target_gdf.crs is None:
        print("  Target CRS missing, assuming EPSG:4326")
        target_gdf.set_crs('EPSG:4326', inplace=True)
    if land_gdf.crs is None:
        print("  Reference CRS missing, assuming EPSG:4326")
        land_gdf.set_crs('EPSG:4326', inplace=True)
        
    if target_gdf.crs != land_gdf.crs:
        print(f"  Aligning CRS to {land_gdf.crs}...")
        target_gdf = target_gdf.to_crs(land_gdf.crs)

    print("\nRunning Coastal Slivers Snapping...")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        cleaned_gdf = enhance_coastlines_with_slivers(target_gdf, land_gdf)

    # Ensure parent output directory exists
    out_dir = os.path.dirname(output_file)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    print(f"\nSaving upscaled GeoJSON to: {output_file}...")
    cleaned_gdf.to_file(output_file, driver='GeoJSON')
    
    in_size = os.path.getsize(input_file) / (1024 * 1024)
    out_size = os.path.getsize(output_file) / (1024 * 1024)
    print(f"✅ Success!")
    print(f"  Input file size:  {in_size:.2f} MB")
    print(f"  Output file size: {out_size:.2f} MB")
    print(f"==========================================\n")


def main():
    parser = argparse.ArgumentParser(
        description="Upscale historical GeoJSON coastlines using high-resolution reference land (Coastal Slivers Snapper)."
    )
    parser.add_argument("-i", "--input", required=True, help="Path to input raw historical GeoJSON")
    parser.add_argument("-o", "--output", required=True, help="Path to output upscaled GeoJSON")
    parser.add_argument("-r", "--reference", required=True, help="Path to high-resolution reference land/coastlines (GeoJSON or Shapefile, e.g. Natural Earth 10m)")

    args = parser.parse_args()
    upscale_map(args.input, args.output, args.reference)


if __name__ == "__main__":
    main()
