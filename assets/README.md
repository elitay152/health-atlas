# Oregon county boundaries

`oregon-counties.geojson` is the Oregon subset of Plotly's `geojson-counties-fips.json`, retrieved October 21, 2026 at commit `0c447c47b757ad74edecab31f0d72f849d2e67c2`:

https://github.com/plotly/datasets/blob/0c447c47b757ad74edecab31f0d72f849d2e67c2/geojson-counties-fips.json

The publisher distributes the dataset repository under the MIT license, reproduced in `BOUNDARIES-LICENSE.txt`. This project filtered feature IDs beginning with `41`, retained all 36 county geometries, and reduced properties to `fips` and `name`. Coordinates are retained as longitude/latitude without reprojection. County geometry has not been independently certified against a current Oregon cadastral source; these generalized boundaries are for visualization, not surveying or legal use. No boundary vintage is inferred from the CDC release year.

Join estimates on five-digit FIPS strings, never display names. The map renders bundled polygons without an external tile provider or runtime GIS download. Missing health records remain gray and do not become zero. Light/dark shades are scaled to available estimates for each selected measure/year, so colors are not directly comparable across measures or years. Copper outlines indicate counties selected in the comparison control.
