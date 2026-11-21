# 🗺️ Historical World Borders GeoJSON (1936)

High-quality, cleaned, and high-resolution historical world boundaries in GeoJSON format for the year **1936** (Interwar / Pre-World War II).

Enhanced with high-fidelity modern coastlines, solving the jagged borders, missing islands, and coastal-sliver issues common in raw historical shapefiles.

![World Map 1936 Preview](preview_1936.png)

---

## 📦 Available Formats

We provide two tiers of resolution to suit different project needs:

| Version | File Size | Description | Best For | Link |
| :--- | :--- | :--- | :--- | :--- |
| **Ultra High-Detail** | **22.5 MB** | Uncompressed, ultra-dense coordinates with sub-kilometer fidelity | 4K video rendering, GIS desktop analysis (QGIS, ArcGIS), high-fidelity browser exploration | [`maps/high-detail/world_1936.geojson`](maps/high-detail/world_1936.geojson) |
| **Standard (Web-Ready)** | **4.5 MB** | Lightweight, smooth, topology-preserving geometry | Web apps (Leaflet, Mapbox, D3.js), mobile apps, fast network transfers | [`maps/standard/world_1936.geojson`](maps/standard/world_1936.geojson) |

> [!TIP]
> **Interactive GitHub Preview**: Both versions render interactively right in GitHub's web interface! You can click on either map file above to pan, zoom, and inspect historical borders directly in your browser.

---

## 📊 Feature Properties

Each country feature in the GeoJSON contains the following properties:

| Property | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `cntry_name` | `string` | Common historical country name | `"France"` |
| `capname` | `string` | Historical capital city name | `"Paris"` |
| `caplat` | `float` | Latitude of capital city | `48.85` |
| `caplong` | `float` | Longitude of capital city | `2.35` |
| `area` | `float` | Territory area | `551500.0` |
| `gwcode` | `int` | Gleditsch-Ward historical state code | `220` |

* **CRS**: Standard GPS coordinates (`EPSG:4326` / WGS84) with `[longitude, latitude]` order (RFC 7946 compliant).

---

## 🚀 Quickstart

### Python (GeoPandas)
```python
import geopandas as gpd
import matplotlib.pyplot as plt

# Load 1936 map
gdf = gpd.read_file("maps/standard/world_1936.geojson")

# Plot
fig, ax = plt.subplots(figsize=(14, 7))
gdf.plot(ax=ax, color="#E4D5B7", edgecolor="#5C4033", linewidth=0.5)
ax.axis("off")
plt.title("World Borders - 1936")
plt.show()
```

### JavaScript (Leaflet)
```javascript
fetch("maps/standard/world_1936.geojson")
  .then(response => response.json())
  .then(data => {
    L.geoJSON(data, {
      style: {
        color: "#5C4033",
        weight: 1,
        fillColor: "#E4D5B7",
        fillOpacity: 0.8
      },
      onEachFeature: (feature, layer) => {
        layer.bindPopup(`<b>${feature.properties.cntry_name}</b><br>Capital: ${feature.properties.capname}`);
      }
    }).addTo(map);
  });
```

---

## 🛠️ Coastline Upscaler Tool (`tools/upscale_coastlines.py`)

Want to upscale your own historical maps? We've open-sourced the standalone tool used to produce the **Ultra High-Detail** map.

### How it Works (The Coastal Slivers Algorithm)
Raw historical datasets often have jagged, coarse coastlines and missing islands. This tool:
1. Compares raw historical borders against modern high-resolution land polygons (e.g. Natural Earth 10m).
2. Isolates the missing coastal slivers and coastal islands.
3. Snaps each coastal sliver to the nearest historical nation using spatial nearest-neighbor joins (`sjoin_nearest`) with deduplication.
4. **Strictly preserves internal historical borders** while elevating coastlines to sub-kilometer resolution.

### Usage
```bash
# 1. Install dependencies
pip install -r tools/requirements.txt

# 2. Run upscaler
python tools/upscale_coastlines.py \
  --input path/to/raw_historical_map.geojson \
  --reference path/to/high_res_land.geojson \
  --output path/to/upscaled_map.geojson
```

---

## 🗺️ Roadmap
We plan to release additional historical eras:
- [x] **1936**: Interwar / Pre-WWII
- [ ] **1914**: Eve of World War I
- [ ] **1939**: Outbreak of World War II
- [ ] **1941**: Axis Expansion Peak
- [ ] **1945**: Post-WWII / Early Cold War
- [ ] **1890**: High Imperialism era

---

## 📄 License & Attribution

- **Data**: Released under [Creative Commons Attribution 4.0 International (CC-BY 4.0)](LICENSE).
- **Sources**: Base historical political entities reference the CShapes dataset and Natural Earth coastlines.
