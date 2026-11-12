# SPDX-License-Identifier: MIT
"""
sparrowqt: dual Qt5/Qt6 compatibility layer for sparrow-wifi.

Strategy:
- Prefer PyQt6 when available (Gentoo, Arch, current Fedora); fall back to
  PyQt5 (Debian/Kali, RPi). This lets one codebase run everywhere during the
  Qt5 -> Qt6 transition without breaking any existing user.
- All enums use fully-qualified names (Qt.AlignmentFlag.AlignCenter) which
  are valid in PyQt5 >= 5.15 and PyQt6 alike.
- QtChart is optional: when unavailable (e.g. Gentoo has no PyQtChart /
  PyQt6-Charts package), the GUI falls back to pyqtgraph for the spectrum
  views. pyqtgraph is pure-python, installable via pip on any distro, and
  faster for real-time spectrum data anyway.

This module exposes a flat namespace that the rest of the codebase imports
from, so individual files never need to know which Qt is underneath.
"""

QT_VERSION = None

# --- Core widget imports -----------------------------------------------------
try:  # PyQt6 first
    from PyQt6.QtCore import (Qt, QTimer, QThread, QSettings, QLocale,
                              QTranslator, QEvent, QObject, QPoint, QPointF,
                              QRect, QRectF, QSize, QSizeF, QDateTime, QDate,
                              QTime, QUrl, QProcess, QSignalMapper,
                              QAbstractItemModel, QFileInfo, QStandardPaths,
                              QSysInfo, QLibraryInfo, QMetaObject, QDir)
    from PyQt6.QtGui import (QIcon, QPixmap, QColor, QBrush, QPen, QPalette, QRegion,
                             QFont, QFontMetrics, QCursor, QCloseEvent,
                             QKeySequence, QAction, QClipboard, QDesktopServices,
                             QValidator, QIntValidator, QDoubleValidator,
                             QPainter, QPdfWriter)
    # QRegularExpression is QtCore in BOTH bindings - unlike QAction, it did
    # not move in Qt6:
    from PyQt6.QtCore import QRegularExpression
    from PyQt6.QtWidgets import (QApplication, QMainWindow,
                                 QFrame, QLabel, QPushButton, QCheckBox,
                                 QComboBox, QLineEdit, QMenu, QMenuBar,
                                 QMessageBox, QFileDialog, QInputDialog,
                                 QTableWidget, QTableWidgetItem, QHeaderView,
                                 QAbstractItemView, QTabWidget, QWidget,
                                 QVBoxLayout, QHBoxLayout, QGridLayout,
                                 QSplitter, QTreeWidget, QTreeWidgetItem,
                                 QScrollArea, QGroupBox, QProgressBar,
                                 QStatusBar, QToolBar, QStyleFactory,
                                 QGraphicsView, QGraphicsScene, QGraphicsItem,
                                 QGraphicsSimpleTextItem, QGraphicsRectItem,
                                 QTextEdit, QPlainTextEdit, QSpinBox,
                                 QDoubleSpinBox, QListWidget, QListWidgetItem,
                                 QStackedWidget, QDockWidget, QSizePolicy,
                                 QSpacerItem, QRadioButton, QButtonGroup,
                                 QSlider, QDial, QScrollBar, QStyle,
                                 QStyleOptionViewItem, QStyledItemDelegate,
                                 QItemDelegate, QDialog, QDialogButtonBox)
    from PyQt6.QtCore import QSortFilterProxyModel
    from PyQt6.QtNetwork import (QUdpSocket, QTcpServer, QTcpSocket,
                                 QHostAddress, QNetworkInterface)
    QT_VERSION = 6
except ImportError:
    from PyQt5.QtCore import (Qt, QTimer, QThread, QSettings, QLocale,
                              QTranslator, QEvent, QObject, QPoint, QPointF,
                              QRect, QRectF, QSize, QSizeF, QDateTime, QDate,
                              QTime, QUrl, QProcess, QSignalMapper,
                              QAbstractItemModel, QFileInfo, QStandardPaths,
                              QSysInfo, QLibraryInfo, QMetaObject, QDir)
    from PyQt5.QtGui import (QIcon, QPixmap, QColor, QBrush, QPen, QPalette, QRegion,
                             QFont, QFontMetrics, QCursor, QCloseEvent,
                             QKeySequence, QClipboard,
                             QDesktopServices, QValidator, QIntValidator,
                             QDoubleValidator,
                             QPainter, QPdfWriter)
    # Names that live in different modules between Qt5 and Qt6:
    from PyQt5.QtWidgets import QAction            # QtGui.QAction in Qt6
    from PyQt5.QtCore import QRegularExpression    # QtCore in both bindings
    from PyQt5.QtWidgets import (QApplication, QMainWindow, QDesktopWidget,
                                 QFrame, QLabel, QPushButton, QCheckBox,
                                 QComboBox, QLineEdit, QMenu, QMenuBar,
                                 QMessageBox, QFileDialog, QInputDialog,
                                 QTableWidget, QTableWidgetItem, QHeaderView,
                                 QAbstractItemView, QTabWidget, QWidget,
                                 QVBoxLayout, QHBoxLayout, QGridLayout,
                                 QSplitter, QTreeWidget, QTreeWidgetItem,
                                 QScrollArea, QGroupBox, QProgressBar,
                                 QStatusBar, QToolBar, QStyleFactory,
                                 QGraphicsView, QGraphicsScene, QGraphicsItem,
                                 QGraphicsSimpleTextItem, QGraphicsRectItem,
                                 QTextEdit, QPlainTextEdit, QSpinBox,
                                 QDoubleSpinBox, QListWidget, QListWidgetItem,
                                 QStackedWidget, QDockWidget, QSizePolicy,
                                 QSpacerItem, QRadioButton, QButtonGroup,
                                 QSlider, QDial, QScrollBar, QStyle,
                                 QStyleOptionViewItem, QStyledItemDelegate,
                                 QItemDelegate,
                                 QDialog, QDialogButtonBox)
    from PyQt5.QtCore import QSortFilterProxyModel  # QtCore in both bindings
    from PyQt5.QtNetwork import (QUdpSocket, QTcpServer, QTcpSocket,
                                 QHostAddress, QNetworkInterface)
    QT_VERSION = 5

# --- Module-object exports -----------------------------------------------------
# Some files do "from sparrowqt import QtCore" / "... QtWidgets"; bind the
# submodule objects themselves, not just their classes.
if QT_VERSION == 6:
    from PyQt6 import QtCore, QtGui, QtWidgets
else:
    from PyQt5 import QtCore, QtGui, QtWidgets

# --- Qt6 enum flattening -------------------------------------------------------
# PyQt5 exposes enum members directly on their classes (Qt.white, QMessageBox.Ok).
# PyQt6 scopes them (Qt.GlobalColor.white, QMessageBox.StandardButton.Ok).
# PyQt5 >= 5.15 also accepts the scoped form, but sparrow-wifi's codebase (and
# every plugin in the wild) uses the legacy flat form. Rather than churn
# hundreds of call sites, hoist the members of the enum classes sparrow-wifi
# uses back up to their legacy names when running under Qt6. This mirrors what
# pyqtgraph does internally for its own Qt6 compatibility layer.
if QT_VERSION == 6:
    def _hoist_enum_members(container, enum_class_names):
        for enum_name in enum_class_names:
            enum_cls = getattr(container, enum_name, None)
            if enum_cls is None:
                continue
            for member in dir(enum_cls):
                if member.startswith('_') or member in ('name', 'value'):
                    continue
                if not hasattr(container, member):
                    try:
                        setattr(container, member, getattr(enum_cls, member))
                    except (AttributeError, TypeError):
                        # Read-only wrapper: member stays scoped. Call sites
                        # that need it will surface in testing.
                        pass

    _hoist_enum_members(Qt, (
        'GlobalColor', 'AlignmentFlag', 'Orientation', 'SortOrder',
        'ItemDataRole', 'ContextMenuPolicy', 'CursorShape', 'MouseButton',
        'CheckState', 'ItemFlag', 'PenStyle', 'BrushStyle', 'WindowType',
        'WindowModality', 'TextInteractionFlag', 'Key', 'CaseSensitivity',
        'ConnectionType', 'TransformationMode', 'AspectRatioMode',
        'WidgetAttribute', 'ScreenOrientation', 'MatchFlag',
        'DockWidgetArea', 'FindChildOption', 'TextFormat', 'DropAction',
        'FillRule', 'KeyboardModifier', 'ImageConversionFlag'))
    _hoist_enum_members(QMessageBox, ('StandardButton',))
    _hoist_enum_members(QDialogButtonBox, ('StandardButton',))
    _hoist_enum_members(QDialog, ('DialogCode',))
    _hoist_enum_members(QPalette, ('ColorRole',))
    _hoist_enum_members(QHeaderView, ('ResizeMode',))
    _hoist_enum_members(QFileDialog, ('Option',))
    _hoist_enum_members(QLineEdit, ('EchoMode',))
    _hoist_enum_members(QAbstractItemView, ('SelectionMode', 'SelectionBehavior',
                                            'EditTrigger', 'ScrollMode', 'ScrollHint'))
    _hoist_enum_members(QGraphicsView, ('ViewportUpdateMode',))
    _hoist_enum_members(QFrame, ('Shape', 'Shadow'))
    _hoist_enum_members(QSizePolicy, ('Policy',))
    _hoist_enum_members(QPainter, ('RenderHint',))
    _hoist_enum_members(QRegion, ('RegionType',))

# --- Optional QtChart ---------------------------------------------------------
# PyQt5.QtChart lives in a separate PyQtChart package on most distros.
# PyQt6 puts charts in PyQt6.QtCharts (if the distro ships it).
# If neither is present, callers should use pyqtgraph instead.
try:
    if QT_VERSION == 6:
        from PyQt6.QtCharts import (QChart, QChartView, QLineSeries,
                                    QValueAxis, QBarSeries, QBarSet,
                                    QBarCategoryAxis, QSplineSeries)
    else:
        from PyQt5.QtChart import (QChart, QChartView, QLineSeries,
                                   QValueAxis, QBarSeries, QBarSet,
                                   QBarCategoryAxis, QSplineSeries)
    HAS_QTCHART = True
except ImportError:
    HAS_QTCHART = False

# --- Optional pyqtgraph fallback ----------------------------------------------
try:
    import pyqtgraph as pg
    HAS_PYQTGRAPH = True
except ImportError:
    HAS_PYQTGRAPH = False


def has_charting():
    """True if any spectrum-charting backend is available."""
    return HAS_QTCHART or HAS_PYQTGRAPH


def charting_backend():
    """'qtchart' or 'pyqtgraph' - or None if neither is importable."""
    if HAS_QTCHART:
        return 'qtchart'
    if HAS_PYQTGRAPH:
        return 'pyqtgraph'
    return None


# --- Qt5/Qt6 divergences ------------------------------------------------------

def available_geometry(widget_or_app=None):
    """Screen geometry in a version-agnostic way.

    QDesktopWidget was removed in Qt6; QScreen is the replacement.
    On Qt5 we use QDesktopWidget().availableGeometry() for parity
    with the original sparrow-wifi code.
    """
    if QT_VERSION >= 6:
        app = widget_or_app
        if app is None:
            app = QApplication.instance()
        if hasattr(app, 'screen'):
            return app.screen().availableGeometry()
        return QApplication.primaryScreen().availableGeometry()
    else:
        return QDesktopWidget().availableGeometry()


def screen_center(widget):
    """Move a widget to the screen center, version-agnostic."""
    if QT_VERSION >= 6:
        geo = widget.frameGeometry()
        center = widget.screen().availableGeometry().center()
        geo.moveCenter(center)
        widget.move(geo.topLeft())
    else:
        geo = widget.frameGeometry()
        center = QDesktopWidget().availableGeometry().center()
        geo.moveCenter(center)
        widget.move(geo.topLeft())
