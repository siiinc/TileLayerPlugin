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
from PyQt4.QtCore import Qt, QSettings
from PyQt4.QtGui import QDialog, QFileDialog

from ui_settingsdialog import Ui_Dialog

class SettingsDialog(QDialog):
  def __init__(self, iface):
    QDialog.__init__(self, iface.mainWindow())
    # set up the user interface
    self.ui = Ui_Dialog()
    self.ui.setupUi(self)
    self.ui.toolButton_externalDirectory.clicked.connect(self.selectExternalDirectory)

    # load settings
    settings = QSettings()
    self.ui.lineEdit_externalDirectory.setText(settings.value("/IdahoLayerPlugin/extDir", "", type=unicode))
    self.ui.spinBox_downloadTimeout.setValue(int(settings.value("/IdahoLayerPlugin/timeout", 30, type=int)))
    self.ui.checkBox_MoveToLayer.setCheckState(int(settings.value("/IdahoLayerPlugin/moveToLayer", 0, type=int)))
    self.ui.checkBox_NavigationMessages.setCheckState(int(settings.value("/IdahoLayerPlugin/naviMsg", Qt.Checked, type=int)))

  def accept(self):
    QDialog.accept(self)

    # save settings
    settings = QSettings()
    settings.setValue("/IdahoLayerPlugin/extDir", self.ui.lineEdit_externalDirectory.text())
    settings.setValue("/IdahoLayerPlugin/timeout", self.ui.spinBox_downloadTimeout.value())
    settings.setValue("/IdahoLayerPlugin/moveToLayer", self.ui.checkBox_MoveToLayer.checkState())
    settings.setValue("/IdahoLayerPlugin/naviMsg", self.ui.checkBox_NavigationMessages.checkState())

  def selectExternalDirectory(self):
    # show select directory dialog
    d  = QFileDialog.getExistingDirectory(self, self.tr("Select external layers directory"), self.ui.lineEdit_externalDirectory.text())
    if d:
      self.ui.lineEdit_externalDirectory.setText(d)
