# TileLayerPlugin

TileLayerPlugin is a plugin to add tiled maps on your map canvas.


## How to use?

TileLayerPlugin is under the Web menu. Only tile frame layers are listed in the add tile layer dialog until you add layer definitions by yourself. You can add available layers by writing a file in the format described below and setting the folder that the file exists as external layer definition directory (If you make it in the layers directory in the plugin, you will lose it when the plugin is updated). A list of prepared layer definition files is [here](https://github.com/minorua/TileLayerPlugin/wiki/Layer-definition-files).

A few layer styles can be changed in the layer properties dialog. You can set sufficient cache size (in kilobytes) in the Network/Cache Settings of the Options dialog in order to make effective use of cache.


### Limitations

* Can display only tiled maps in the format described in [Slippy map tilenames](http://wiki.openstreetmap.org/wiki/Slippy_map_tilenames) and similar tiled maps that y-axis of the tile matrix is inverted. Tile size should be 256 x 256.


### Layer definition file format

Layer definition file is a text file. Each line has information for a tile layer. Fields are separated with tab character. The file extension is **tsv** and the file encoding is UTF-8.

**Line format is:**  
`title	attribution	url	yOriginTop	zmin	zmax	xmin	ymin	xmax	ymax`

**Description of fields:**  
Required
* title: Layer title
* attribution: Attribution specified by tile map service provider.
* url: Template URL of tiled map. Special strings "{x}", "{y}" and "{z}" will be replaced with tile coordinates and zoom level that are calculated with current map view.

Options
* yOriginTop: Origin location of tile matrix. 1 if origin is top-left (similar to Slippy Map), 0 if origin is bottom-left (similar to TMS). Default is 1.
* zmin, zmax: Minimum/Maximum value of zoom level. Default values: zmin=0, zmax=18.
* xmin, ymin, xmax, ymax: Layer extent in degrees (longitude/latitude). Note: Valid range of y in Pseudo Mercator projection is from about -85.05 to about 85.05.

Notes
* You should correctly set zmin, zmax, xmin, ymin, xmax and ymax in order not to send requests for absent tiles to the server.
* You SHOULD obey the Terms of Use of tile map service.


### Examples of layer definition file
* **For a tiled map provided by a web server**  
freetilemap.tsv  
`RoadMap	FreeTileMap	http://freetilemap.example.com/road/{z}/{x}/{y}.png`

* **For a tiled map generated by gdal2tiles.py**  
slope.tsv  
`slope	local	file:///d:/tilemaps/slope/{z}/{x}/{y}.png	0	6	13	130.5	33.6	135.0	36.0`

Note: Use tab character to separate fields!


## Known issue(s)

* Credit label is not printed in the correct position in some projections. No problem in the Mercator projection.


## Adding a TileLayer from Python

```python
plugin = qgis.utils.plugins.get("TileLayerPlugin")
if plugin:
  from TileLayerPlugin.tiles import BoundingBox, TileLayerDefinition
  bbox = None    # BoundingBox(-180, -85.05, 180, 85.05)
  layerdef = TileLayerDefinition(u"title",
                                 u"attribution",
                                 "http://example.com/xyz/{z}/{x}/{y}.png",
                                 zmin=1,
                                 zmax=18,
                                 bbox=bbox)
  plugin.addTileLayer(layerdef)
else:
  from PyQt4.QtGui import QMessageBox
  QMessageBox.warning(None,
                      u"TileLayerPlugin not installed",
                      u"Please install it and try again.")
```


## ChangeLog

version 0.60
* Map rotation support
* Added function (API) to add tile layer from Python
* Souce code clean-up

version 0.50.1  
* TileLayerPlugin doesn't support map rotation now. Shows message and does not render tiles if map canvas is rotated.

version 0.50  
* Reprojection support

version 0.40  
* Moved to the web menu.
* Moved settings to add layer dialog.
* Default range of zoom changed to [0, 18].
* Print quality improvement

version 0.30  
* Fixed "Could not draw" error that occurs in 64-bit QGIS (OSGeo4W64).
* Adapted to multi-thread rendering.

version 0.20  
* Layer information file extension was limited to tsv.
* providerName field was renamed to credit, and so on.

## License
TileLayerPlugin is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation; either version 2 of the License, or (at your option) any later version.

_Copyright (c) 2013 Minoru Akagi_
