# -*- coding: utf-8 -*-
"""
/***************************************************************************
 IdahoLayer Plugin
                                 A QGIS plugin
 Plugin layer for Tile Maps
                              -------------------
        begin                : 2012-12-16
        copyright            : (C) 2013 by Minoru Akagi
        email                : akaginch@gmail.com
 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""
import math
import os
import threading
from PyQt4.QtCore import Qt, QEventLoop, QFile, QObject, QPoint, QPointF, QRect, QRectF, QSettings, QUrl, QTimer, \
    pyqtSignal, qDebug
from PyQt4.QtGui import QBrush, QColor, QFont, QImage, QPainter, QMessageBox
from qgis.core import QGis, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry, QgsPluginLayer, \
    QgsPluginLayerType, QgsRectangle
from qgis.gui import QgsMessageBar

try:
    from osgeo import gdal

    hasGdal = True
except:
    hasGdal = False

from downloader import Downloader
from rotatedrect import RotatedRect
from tiles import BoundingBox, Tile, TileDefaultSettings, IdahoLayerDefinition, Tiles

debug_mode = 0


class IdahoLayer(QgsPluginLayer):
    LAYER_TYPE = "IdahoLayer"
    MAX_TILE_COUNT = 256
    DEFAULT_BLEND_MODE = "SourceOver"
    DEFAULT_SMOOTH_RENDER = True

    # PyQt signals
    fetchRequestSignal = pyqtSignal(list)
    statusSignal = pyqtSignal(str, int)
    messageBarSignal = pyqtSignal(str, str, int, int)

    def __init__(self, plugin, layerDef, creditVisibility=1):
        QgsPluginLayer.__init__(self, IdahoLayer.LAYER_TYPE, layerDef.title)
        self.plugin = plugin
        self.iface = plugin.iface
        self.layerDef = layerDef
        self.creditVisibility = 1 if creditVisibility else 0
        self.tiles = None

        # set attribution property
        self.setAttribution(layerDef.attribution)

        # set custom properties
        self.setCustomProperty("title", layerDef.title)
        self.setCustomProperty("credit", layerDef.attribution)
        self.setCustomProperty("serviceUrl", layerDef.serviceUrl)
        self.setCustomProperty("yOriginTop", layerDef.yOriginTop)
        self.setCustomProperty("zmin", layerDef.zmin)
        self.setCustomProperty("zmax", layerDef.zmax)
        if layerDef.bbox:
            self.setCustomProperty("bbox", layerDef.bbox.toString())
        self.setCustomProperty("creditVisibility", self.creditVisibility)

        # set crs
        if plugin.crs3857 is None:
            # create a QgsCoordinateReferenceSystem instance if plugin has no instance yet
            plugin.crs3857 = QgsCoordinateReferenceSystem(3857)

        self.setCrs(plugin.crs3857)

        # set extent
        if layerDef.bbox:
            if not layerDef.epsg:
                layerDef.epsg = 4326
            if layerDef.epsg == 3857 or layerDef.epsg == 900913:
                self.setExtent(layerDef.bbox.toQgsRectangle())
            else:
                self.setExtent(BoundingBox.epsgToMercatorMeters(layerDef.bbox, layerDef.epsg).toQgsRectangle())
        else:
            self.setExtent(QgsRectangle(-layerDef.TSIZE1, -layerDef.TSIZE1, layerDef.TSIZE1, layerDef.TSIZE1))

        # set styles
        self.setTransparency(0)
        self.setBlendModeByName(self.DEFAULT_BLEND_MODE)
        self.setSmoothRender(self.DEFAULT_SMOOTH_RENDER)

        # downloader
        maxConnections = HonestAccess.maxConnections(layerDef.serviceUrl)
        cacheExpiry = QSettings().value("/qgis/defaultTileExpiry", 24, type=int)
        userAgent = "QGIS/{0} IdahoLayerPlugin/{1}".format(QGis.QGIS_VERSION,
                                                          self.plugin.VERSION)  # will be overwritten in QgsNetworkAccessManager::createRequest() since 2.2
        self.downloader = Downloader(self, maxConnections, cacheExpiry, userAgent)
        if self.iface:
            self.downloader.replyFinished.connect(self.networkReplyFinished)  # download progress

        # TOS violation warning
        if HonestAccess.restrictedByTOS(layerDef.serviceUrl):
            QMessageBox.warning(None,
                                u"{0} - {1}".format(self.tr("IdahoLayerPlugin"), layerDef.title),
                                self.tr("Access to the service is restricted by the TOS. Please follow the TOS."))

        # multi-thread rendering
        self.eventLoop = None
        self.fetchRequestSignal.connect(self.fetchRequestSlot)
        if self.iface:
            self.statusSignal.connect(self.showStatusMessageSlot)
            self.messageBarSignal.connect(self.showMessageBarSlot)

        self.setValid(True)

    def setBlendModeByName(self, modeName):
        self.blendModeName = modeName
        blendMode = getattr(QPainter, "CompositionMode_" + modeName, 0)
        self.setBlendMode(blendMode)
        self.setCustomProperty("blendMode", modeName)

    def setTransparency(self, transparency):
        self.transparency = transparency
        self.setCustomProperty("transparency", transparency)

    def setSmoothRender(self, isSmooth):
        self.smoothRender = isSmooth
        self.setCustomProperty("smoothRender", 1 if isSmooth else 0)

    def setCreditVisibility(self, visible):
        self.creditVisibility = visible
        self.setCustomProperty("creditVisibility", 1 if visible else 0)

    def draw(self, renderContext):
        self.renderContext = renderContext
        extent = renderContext.extent()
        if extent.isEmpty() or extent.width() == float("inf"):
            qDebug("Drawing is skipped because map extent is empty or inf.")
            return True

        map2pixel = renderContext.mapToPixel()
        mupp = map2pixel.mapUnitsPerPixel()
        rotation = map2pixel.mapRotation() if self.plugin.apiChanged27 else 0

        painter = renderContext.painter()
        viewport = painter.viewport()

        mpp = mupp  # meters per pixel
        isWebMercator = self.isProjectCrsWebMercator()

        # frame layer isn't drawn if the CRS is not web mercator or map is rotated
        if self.layerDef.serviceUrl[
            0] == ":" and "frame" in self.layerDef.serviceUrl:  # or "number" in self.layerDef.serviceUrl:
            msg = ""
            if not isWebMercator:
                msg = self.tr("Frame layer is not drawn if the CRS is not EPSG:3857")
            elif rotation:
                msg = self.tr("Frame layer is not drawn if map is rotated")

            if msg:
                self.showMessageBar(msg, QgsMessageBar.INFO, 2)
                return True

        if not isWebMercator:
            # get extent in project CRS
            cx, cy = 0.5 * viewport.width(), 0.5 * viewport.height()
            center = map2pixel.toMapCoordinatesF(cx, cy)
            mapExtent = RotatedRect(center, mupp * viewport.width(), mupp * viewport.height(), rotation)

            transform = renderContext.coordinateTransform()
            if transform:
                transform = QgsCoordinateTransform(transform.destCRS(),
                                                   transform.sourceCrs())  # project CRS to layer CRS (EPSG:3857)
                geometry = QgsGeometry.fromPolyline(
                    [map2pixel.toMapCoordinatesF(cx - 0.5, cy), map2pixel.toMapCoordinatesF(cx + 0.5, cy)])
                geometry.transform(transform)
                mpp = geometry.length()

                # get bounding box of the extent in EPSG:3857
                geometry = mapExtent.geometry()
                geometry.transform(transform)
                extent = geometry.boundingBox()
            else:
                qDebug("Drawing is skipped because CRS transformation is not ready.")
                return True

        elif rotation:
            # get bounding box of the extent
            mapExtent = RotatedRect(extent.center(), mupp * viewport.width(), mupp * viewport.height(), rotation)
            extent = mapExtent.boundingBox()

        # calculate zoom level
        tile_mpp1 = self.layerDef.TSIZE1 / self.layerDef.TILE_SIZE
        zoom = int(math.ceil(math.log(tile_mpp1 / mpp, 2) + 1))
        zoom = max(0, min(zoom, self.layerDef.zmax))
        # zoom = max(self.layerDef.zmin, zoom)

        # zoom limit
        if zoom < self.layerDef.zmin:
            if self.plugin.navigationMessagesEnabled:
                msg = self.tr("Current zoom level ({0}) is smaller than zmin ({1}): {2}").format(zoom,
                                                                                                 self.layerDef.zmin,
                                                                                                 self.layerDef.title)
                self.showMessageBar(msg, QgsMessageBar.INFO, 2)
            return True

        while True:
            # calculate tile range (yOrigin is top)
            size = self.layerDef.TSIZE1 / 2 ** (zoom - 1)
            matrixSize = 2 ** zoom
            ulx = max(0, int((extent.xMinimum() + self.layerDef.TSIZE1) / size))
            uly = max(0, int((self.layerDef.TSIZE1 - extent.yMaximum()) / size))
            lrx = min(int((extent.xMaximum() + self.layerDef.TSIZE1) / size), matrixSize - 1)
            lry = min(int((self.layerDef.TSIZE1 - extent.yMinimum()) / size), matrixSize - 1)

            # bounding box limit
            if self.layerDef.bbox:
                if not self.layerDef.epsg:
                    self.layerDef.epsg = 4326
                elif self.layerDef.epsg == 3857 or self.layerDef.epsg == 900913:
                    trange = self.layerDef.bboxMercatorToTileRange(zoom, self.layerDef.bbox)
                else:
                    trange = self.layerDef.epsgToTileRange(zoom, self.layerDef.bbox)
                ulx = max(ulx, trange.xmin)
                uly = max(uly, trange.ymin)
                lrx = min(lrx, trange.xmax)
                lry = min(lry, trange.ymax)
                if lrx < ulx or lry < uly:
                    # tile range is out of the bounding box
                    return True

            # tile count limit
            tileCount = (lrx - ulx + 1) * (lry - uly + 1)
            if tileCount > self.MAX_TILE_COUNT:
                # as tile count is over the limit, decrease zoom level
                zoom -= 1

                # if the zoom level is less than the minimum, do not draw
                if zoom < self.layerDef.zmin:
                    msg = self.tr("Tile count is over limit ({0}, max={1})").format(tileCount, self.MAX_TILE_COUNT)
                    self.showMessageBar(msg, QgsMessageBar.WARNING, 4)
                    return True
                continue

            # zoom level has been determined
            break

        self.logT("IdahoLayer.draw: {0} {1} {2} {3} {4}".format(zoom, ulx, uly, lrx, lry))

        # save painter state
        painter.save()

        # set pen and font
        painter.setPen(Qt.black)
        font = QFont(painter.font())
        font.setPointSize(10)
        painter.setFont(font)

        if self.layerDef.serviceUrl[0] == ":":
            painter.setBrush(QBrush(Qt.NoBrush))
            self.drawDebugInfo(renderContext, zoom, ulx, uly, lrx, lry)
        else:
            # create Tiles class object and throw url into it
            tiles = Tiles(zoom, ulx, uly, lrx, lry, self.layerDef)
            urls = []
            cacheHits = 0
            for ty in range(uly, lry + 1):
                for tx in range(ulx, lrx + 1):
                    data = None
                    url = self.layerDef.tileUrl(zoom, tx, ty)
                    if self.tiles and zoom == self.tiles.zoom and url in self.tiles.tiles:
                        data = self.tiles.tiles[url].data
                    tiles.addTile(url, Tile(zoom, tx, ty, data))
                    if data is None:
                        urls.append(url)
                    elif data:  # memory cache exists
                        cacheHits += 1
                        # else:    # tile not found

            self.tiles = tiles
            if len(urls) > 0:
                # fetch tile data
                files = self.fetchFiles(urls)
                for url in files.keys():
                    self.tiles.setImageData(url, files[url])

                if self.iface:
                    stats = self.downloader.stats()
                    allCacheHits = cacheHits + stats["cacheHits"]
                    msg = self.tr("{0} files downloaded. {1} caches hit.").format(stats["downloaded"], allCacheHits)
                    barmsg = None
                    if self.downloader.errorStatus != Downloader.NO_ERROR:
                        if self.downloader.errorStatus == Downloader.TIMEOUT_ERROR:
                            barmsg = self.tr("Download Timeout - {0}").format(self.name())
                        else:
                            msg += self.tr(" {0} files failed.").format(stats["errors"])
                            if stats["successed"] + allCacheHits == 0:
                                barmsg = self.tr("Failed to download all {0} files. - {1}").format(stats["errors"],
                                                                                                   self.name())
                    self.showStatusMessage(msg, 5000)
                    if barmsg:
                        self.showMessageBar(barmsg, QgsMessageBar.WARNING, 4)

            # apply layer style
            oldOpacity = painter.opacity()
            painter.setOpacity(0.01 * (100 - self.transparency))
            oldSmoothRenderHint = painter.testRenderHint(QPainter.SmoothPixmapTransform)
            if self.smoothRender:
                painter.setRenderHint(QPainter.SmoothPixmapTransform)

            # draw tiles
            if isWebMercator and rotation == 0:
                # no need to reproject tiles
                self.drawTiles(renderContext, self.tiles)
                # self.drawTilesDirectly(renderContext, self.tiles)
            else:
                # reproject tiles
                self.drawTilesOnTheFly(renderContext, mapExtent, self.tiles)

            # restore layer style
            painter.setOpacity(oldOpacity)
            if self.smoothRender:
                painter.setRenderHint(QPainter.SmoothPixmapTransform, oldSmoothRenderHint)

            # draw credit on the bottom right corner
            if self.creditVisibility and self.layerDef.attribution:
                margin, paddingH, paddingV = (3, 4, 3)
                # scale
                scaleX, scaleY = self.getScaleToVisibleExtent(renderContext)
                scale = max(scaleX, scaleY)
                painter.scale(scale, scale)

                visibleSWidth = painter.viewport().width() * scaleX / scale
                visibleSHeight = painter.viewport().height() * scaleY / scale
                rect = QRect(0, 0, visibleSWidth - margin, visibleSHeight - margin)
                textRect = painter.boundingRect(rect, Qt.AlignBottom | Qt.AlignRight, self.layerDef.attribution)
                bgRect = QRect(textRect.left() - paddingH, textRect.top() - paddingV, textRect.width() + 2 * paddingH,
                               textRect.height() + 2 * paddingV)
                painter.fillRect(bgRect, QColor(240, 240, 240, 150))  # 197, 234, 243, 150))
                painter.drawText(rect, Qt.AlignBottom | Qt.AlignRight, self.layerDef.attribution)

        # restore painter state
        painter.restore()

        return True

    def drawTiles(self, renderContext, tiles, sdx=1.0, sdy=1.0):
        # create an image that has the same resolution as the tiles
        image = tiles.image()

        # tile extent to pixel
        map2pixel = renderContext.mapToPixel()
        extent = tiles.extent()
        topLeft = map2pixel.transform(extent.xMinimum(), extent.yMaximum())
        bottomRight = map2pixel.transform(extent.xMaximum(), extent.yMinimum())
        rect = QRectF(QPointF(topLeft.x() * sdx, topLeft.y() * sdy),
                      QPointF(bottomRight.x() * sdx, bottomRight.y() * sdy))

        # draw the image on the map canvas
        renderContext.painter().drawImage(rect, image)

        self.log("Tiles extent: " + str(extent))
        self.log("Draw into canvas rect: " + str(rect))

    def drawTilesOnTheFly(self, renderContext, mapExtent, tiles, sdx=1.0, sdy=1.0):
        if not hasGdal:
            msg = self.tr("Rotation/Reprojection requires python-gdal")
            self.showMessageBar(msg, QgsMessageBar.INFO, 2)
            return

        transform = renderContext.coordinateTransform()
        if transform:
            sourceCrs = transform.sourceCrs()
            destCrs = transform.destCRS()
        else:
            sourceCrs = destCrs = self.crs()

        # create image from the tiles
        image = tiles.image()

        # tile extent
        extent = tiles.extent()
        geotransform = [extent.xMinimum(), extent.width() / image.width(), 0, extent.yMaximum(), 0,
                        -extent.height() / image.height()]

        # source raster dataset
        driver = gdal.GetDriverByName("MEM")
        tile_ds = driver.Create("", image.width(), image.height(), 1, gdal.GDT_UInt32)
        tile_ds.SetProjection(str(sourceCrs.toWkt()))
        tile_ds.SetGeoTransform(geotransform)

        # QImage to raster
        ba = image.bits().asstring(image.numBytes())
        tile_ds.GetRasterBand(1).WriteRaster(0, 0, image.width(), image.height(), ba)

        # target raster size - if smoothing is enabled, create raster of twice each of width and height of viewport size
        # in order to get high quality image
        oversampl = 2 if self.smoothRender else 1

        painter = renderContext.painter()
        viewport = painter.viewport()
        width, height = viewport.width() * oversampl, viewport.height() * oversampl

        # target raster dataset
        canvas_ds = driver.Create("", width, height, 1, gdal.GDT_UInt32)
        canvas_ds.SetProjection(str(destCrs.toWkt()))
        canvas_ds.SetGeoTransform(mapExtent.geotransform(width, height, is_grid_point=False))

        # reproject image
        gdal.ReprojectImage(tile_ds, canvas_ds)

        # raster to QImage
        ba = canvas_ds.GetRasterBand(1).ReadRaster(0, 0, width, height)
        reprojected_image = QImage(ba, width, height, QImage.Format_ARGB32_Premultiplied)

        # draw the image on the map canvas
        rect = QRectF(QPointF(0, 0), QPointF(viewport.width() * sdx, viewport.height() * sdy))
        painter.drawImage(rect, reprojected_image)

    def drawTilesDirectly(self, renderContext, tiles, sdx=1.0, sdy=1.0):
        p = renderContext.painter()
        for url, tile in tiles.tiles.items():
            self.log("Draw tile: zoom: %d, x:%d, y:%d, data:%s" % (tile.zoom, tile.x, tile.y, str(tile.data)))
            rect = self.getTileRect(renderContext, tile.zoom, tile.x, tile.y, sdx, sdy)
            if tile.data:
                image = QImage()
                image.loadFromData(tile.data)
                p.drawImage(rect, image)

    def drawDebugInfo(self, renderContext, zoom, ulx, uly, lrx, lry):
        painter = renderContext.painter()
        scaleX, scaleY = self.getScaleToVisibleExtent(renderContext)
        painter.scale(scaleX, scaleY)

        if "frame" in self.layerDef.serviceUrl:
            self.drawFrames(renderContext, zoom, ulx, uly, lrx, lry, 1.0 / scaleX, 1.0 / scaleY)
        if "number" in self.layerDef.serviceUrl:
            self.drawNumbers(renderContext, zoom, ulx, uly, lrx, lry, 1.0 / scaleX, 1.0 / scaleY)
        if "info" in self.layerDef.serviceUrl:
            self.drawInfo(renderContext, zoom, ulx, uly, lrx, lry)

    def drawFrame(self, renderContext, zoom, x, y, sdx, sdy):
        rect = self.getTileRect(renderContext, zoom, x, y, sdx, sdy)
        p = renderContext.painter()
        # p.drawRect(rect)   # A slash appears on the top-right tile without Antialiasing render hint.
        pts = [rect.topLeft(), rect.topRight(), rect.bottomRight(), rect.bottomLeft(), rect.topLeft()]
        for i in range(4):
            p.drawLine(pts[i], pts[i + 1])

    def drawFrames(self, renderContext, zoom, xmin, ymin, xmax, ymax, sdx, sdy):
        for y in range(ymin, ymax + 1):
            for x in range(xmin, xmax + 1):
                self.drawFrame(renderContext, zoom, x, y, sdx, sdy)

    def drawNumber(self, renderContext, zoom, x, y, sdx, sdy):
        rect = self.getTileRect(renderContext, zoom, x, y, sdx, sdy)
        p = renderContext.painter()
        if not self.layerDef.yOriginTop:
            y = (2 ** zoom - 1) - y
        p.drawText(rect, Qt.AlignCenter, "(%d, %d)\nzoom: %d" % (x, y, zoom));

    def drawNumbers(self, renderContext, zoom, xmin, ymin, xmax, ymax, sdx, sdy):
        for y in range(ymin, ymax + 1):
            for x in range(xmin, xmax + 1):
                self.drawNumber(renderContext, zoom, x, y, sdx, sdy)

    def drawInfo(self, renderContext, zoom, xmin, ymin, xmax, ymax):
        from debuginfo import drawDebugInformation
        drawDebugInformation(self, renderContext, zoom, xmin, ymin, xmax, ymax)

    def getScaleToVisibleExtent(self, renderContext):
        mapSettings = self.iface.mapCanvas().mapSettings() if self.plugin.apiChanged23 else self.iface.mapCanvas().mapRenderer()
        painter = renderContext.painter()
        if painter.device().logicalDpiX() == mapSettings.outputDpi():
            return 1.0, 1.0  # scale should be 1.0 in rendering on map canvas

        extent = renderContext.extent()
        ct = renderContext.coordinateTransform()
        if ct:
            # FIX ME: want to get original visible extent in project CRS or visible view size in pixels

            # extent = ct.transformBoundingBox(extent)
            # xmax, ymin = extent.xMaximum(), extent.yMinimum()

            pt1 = ct.transform(extent.xMaximum(), extent.yMaximum())
            pt2 = ct.transform(extent.xMaximum(), extent.yMinimum())
            pt3 = ct.transform(extent.xMinimum(), extent.yMinimum())
            xmax, ymin = min(pt1.x(), pt2.x()), max(pt2.y(), pt3.y())
        else:
            xmax, ymin = extent.xMaximum(), extent.yMinimum()

        bottomRight = renderContext.mapToPixel().transform(xmax, ymin)
        viewport = painter.viewport()
        scaleX = bottomRight.x() / viewport.width()
        scaleY = bottomRight.y() / viewport.height()
        return scaleX, scaleY

    def getTileRect(self, renderContext, zoom, x, y, sdx=1.0, sdy=1.0, toInt=True):
        """ get tile pixel rect in the render context """
        r = self.layerDef.getTileRect(zoom, x, y)
        map2pix = renderContext.mapToPixel()
        topLeft = map2pix.transform(r.xMinimum(), r.yMaximum())
        bottomRight = map2pix.transform(r.xMaximum(), r.yMinimum())
        if toInt:
            return QRect(QPoint(round(topLeft.x() * sdx), round(topLeft.y() * sdy)),
                         QPoint(round(bottomRight.x() * sdx), round(bottomRight.y() * sdy)))
        else:
            return QRectF(QPointF(topLeft.x() * sdx, topLeft.y() * sdy),
                          QPointF(bottomRight.x() * sdx, bottomRight.y() * sdy))

    def isProjectCrsWebMercator(self):
        mapSettings = self.iface.mapCanvas().mapSettings() if self.plugin.apiChanged23 else self.iface.mapCanvas().mapRenderer()
        return mapSettings.destinationCrs().postgisSrid() == 3857

    def networkReplyFinished(self, url):
        # show progress
        stats = self.downloader.stats()
        msg = self.tr("{0} of {1} files downloaded.").format(stats["downloaded"], stats["total"])
        errors = stats["errors"]
        if errors:
            msg += self.tr(" {0} files failed.").format(errors)
        self.showStatusMessage(msg)

    def readXml(self, node):
        self.readCustomProperties(node)
        self.layerDef.title = self.customProperty("title", "")
        self.layerDef.attribution = self.customProperty("credit", "")
        if self.layerDef.attribution == "":
            self.layerDef.attribution = self.customProperty("providerName", "")  # for compatibility with 0.11
        self.layerDef.serviceUrl = self.customProperty("serviceUrl", "")
        self.layerDef.yOriginTop = int(self.customProperty("yOriginTop", 1))
        self.layerDef.zmin = int(self.customProperty("zmin", TileDefaultSettings.ZMIN))
        self.layerDef.zmax = int(self.customProperty("zmax", TileDefaultSettings.ZMAX))
        bbox = self.customProperty("bbox", None)
        if bbox:
            if not self.layerDef.epsg:
                self.layerDef.epsg = 4326
            self.layerDef.bbox = BoundingBox.fromString(bbox)
            self.setExtent(BoundingBox.epsgToMercatorMeters(self.layerDef.bbox).toQgsRectangle())

        # layer style
        self.setTransparency(int(self.customProperty("transparency", 0)))
        self.setBlendModeByName(self.customProperty("blendMode", self.DEFAULT_BLEND_MODE))
        self.setSmoothRender(int(self.customProperty("smoothRender", self.DEFAULT_SMOOTH_RENDER)))
        self.creditVisibility = int(self.customProperty("creditVisibility", 1))

        # max connections of downloader
        self.downloader.maxConnections = HonestAccess.maxConnections(self.layerDef.serviceUrl)
        return True

    def writeXml(self, node, doc):
        element = node.toElement();
        element.setAttribute("type", "plugin")
        element.setAttribute("name", IdahoLayer.LAYER_TYPE);
        return True

    def readSymbology(self, node, errorMessage):
        return False

    def writeSymbology(self, node, doc, errorMessage):
        return False

    def metadata(self):
        lines = []
        fmt = u"%s:\t%s"
        lines.append(fmt % (self.tr("Title"), self.layerDef.title))
        lines.append(fmt % (self.tr("Attribution"), self.layerDef.attribution))
        lines.append(fmt % (self.tr("URL"), self.layerDef.serviceUrl))
        lines.append(fmt % (self.tr("yOrigin"), u"%s (yOriginTop=%d)" % (
        ("Bottom", "Top")[self.layerDef.yOriginTop], self.layerDef.yOriginTop)))
        if self.layerDef.bbox:
            extent = self.layerDef.bbox.toString()
        else:
            extent = self.tr("Not set")
        lines.append(fmt % (self.tr("Zoom range"), "%d - %d" % (self.layerDef.zmin, self.layerDef.zmax)))
        lines.append(fmt % (self.tr("Layer Extent"), extent))
        return "\n".join(lines)

    # functions for multi-thread rendering
    def fetchFiles(self, urls):
        if not self.plugin.apiChanged23:
            return self.downloader.fetchFiles(urls, self.plugin.downloadTimeout)

        self.logT("IdahoLayer.fetchFiles() starts")
        # create a QEventLoop object that belongs to the current worker thread
        eventLoop = QEventLoop()
        self.downloader.allRepliesFinished.connect(eventLoop.quit)

        # create a timer to watch whether rendering is stopped
        watchTimer = QTimer()
        watchTimer.timeout.connect(eventLoop.quit)

        # send a fetch request to the main thread
        self.fetchRequestSignal.emit(urls)

        # wait for the fetch to finish
        tick = 0
        interval = 500
        timeoutTick = self.plugin.downloadTimeout * 1000 / interval
        watchTimer.start(interval)
        while tick < timeoutTick:
            # run event loop for 0.5 seconds at maximum
            eventLoop.exec_()
            if self.downloader.unfinishedCount() == 0 or self.renderContext.renderingStopped():
                break
            tick += 1
        watchTimer.stop()

        if tick == timeoutTick and self.downloader.unfinishedCount() > 0:
            self.log("fetchFiles timeout")
            self.downloader.abort()
            self.downloader.errorStatus = Downloader.TIMEOUT_ERROR
        files = self.downloader.fetchedFiles

        watchTimer.timeout.disconnect(eventLoop.quit)  #
        self.downloader.allRepliesFinished.disconnect(eventLoop.quit)

        self.logT("IdahoLayer.fetchFiles() ends")
        return files

    def fetchRequestSlot(self, urls):
        self.downloader.fetchFilesAsync(urls, self.plugin.downloadTimeout)

    def showStatusMessage(self, msg, timeout=0):
        self.statusSignal.emit(msg, timeout)

    def showStatusMessageSlot(self, msg, timeout):
        self.iface.mainWindow().statusBar().showMessage(msg, timeout)

    def showMessageBar(self, text, level=QgsMessageBar.INFO, duration=0, title=None):
        if title is None:
            title = self.plugin.pluginName
        self.messageBarSignal.emit(title, text, level, duration)

    def showMessageBarSlot(self, title, text, level, duration):
        self.iface.messageBar().pushMessage(title, text, level, duration)

    def log(self, msg):
        if debug_mode:
            qDebug(msg)

    def logT(self, msg):
        if debug_mode:
            qDebug("%s: %s" % (str(threading.current_thread()), msg))

    def dump(self, detail=False, bbox=None):
        pass


# def createMapRenderer(self, renderContext):
#    qDebug("createMapRenderer")
#    self.renderer = QgsPluginLayerRenderer(self, renderContext)
#    return self.renderer

class IdahoLayerType(QgsPluginLayerType):
    def __init__(self, plugin):
        QgsPluginLayerType.__init__(self, IdahoLayer.LAYER_TYPE)
        self.plugin = plugin

    def createLayer(self):
        return IdahoLayer(self.plugin, IdahoLayerDefinition.createEmptyInfo())

    def showLayerProperties(self, layer):
        from propertiesdialog import PropertiesDialog
        dialog = PropertiesDialog(layer)
        dialog.applyClicked.connect(self.applyClicked)
        dialog.show()
        accepted = dialog.exec_()
        if accepted:
            self.applyProperties(dialog)
        return True

    def applyClicked(self):
        self.applyProperties(QObject().sender())

    def applyProperties(self, dialog):
        layer = dialog.layer
        layer.setTransparency(dialog.ui.spinBox_Transparency.value())
        layer.setBlendModeByName(dialog.ui.comboBox_BlendingMode.currentText())
        layer.setSmoothRender(dialog.ui.checkBox_SmoothRender.isChecked())
        layer.setCreditVisibility(dialog.ui.checkBox_CreditVisibility.isChecked())
        layer.repaintRequested.emit()


class HonestAccess:
    @staticmethod
    def maxConnections(url):
        host = QUrl(url).host()
        if "openstreetmap.org" in host:  # http://wiki.openstreetmap.org/wiki/Tile_servers
            return 2  # http://wiki.openstreetmap.org/wiki/Tile_usage_policy
        return 6

    @staticmethod
    def restrictedByTOS(url):
        # whether access to the url is restricted by TOS
        host = QUrl(url).host()
        if "google.com" in host:  # https://developers.google.com/maps/terms 10.1.1.a No Access to Maps API(s) Except...
            return True
        return False
