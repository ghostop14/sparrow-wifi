# SPDX-License-Identifier: MIT
"""
sparrowchart: QChart-compatible API backed by pyqtgraph.

When PyQt5.QtChart / PyQt6.QtCharts is available (Kali, RPi, pip-installed),
the real QtChart classes are re-exported and zero code paths change. When
QtChart is unavailable (Gentoo, minimal installs), the same API is emulated
with pyqtgraph, which is faster for real-time spectrum data anyway.

Usage in application code is unchanged:
    from sparrowchart import SparrowChart, SparrowChartView, SparrowLineSeries, SparrowValueAxis
"""

from sparrowqt import HAS_QTCHART, HAS_PYQTGRAPH, QT_VERSION, Qt

if HAS_QTCHART:
    # Real QtChart available - use it directly. QT_VERSION (computed in
    # sparrowqt from which binding actually imported) is the only reliable
    # discriminator: PyQt5 >= 5.15 also exposes scoped enums such as
    # Qt.AlignmentFlag, so a hasattr probe cannot distinguish the bindings.
    if QT_VERSION == 6:
        # Qt6 path
        from PyQt6.QtCharts import (QChart, QChartView, QLineSeries,
                                    QValueAxis, QSplineSeries)

        class _Qt6ChartCompat(QChart):
            """QChart with the Qt5-style axisX()/axisY() convenience accessors
            that Qt 6 removed; the application code (and the pyqtgraph fallback
            below) still uses them. Returns the first axis of the requested
            orientation, or None if the chart has none - matching Qt5."""
            def axisX(self):
                axes = self.axes(Qt.Orientation.Horizontal)
                return axes[0] if axes else None

            def axisY(self):
                axes = self.axes(Qt.Orientation.Vertical)
                return axes[0] if axes else None

        SparrowChart = _Qt6ChartCompat

        class _Qt6LineSeriesCompat(QLineSeries):
            """QLineSeries with the Qt5-style pointsVector() accessor that
            Qt 6 removed (QVector merged into QList in Qt6; points() is the
            replacement and returns the same QPointF list). The append(x,y),
            replace(index,x,y) and remove(index) overloads the application
            uses were verified to still exist on the real Qt6 series."""
            def pointsVector(self):
                return self.points()

        SparrowLineSeries = _Qt6LineSeriesCompat
    else:
        # Qt5 path
        from PyQt5.QtChart import (QChart, QChartView, QLineSeries,
                                   QValueAxis, QSplineSeries)
        SparrowChart = QChart            # axisX()/axisY() are native in Qt5
        SparrowLineSeries = QLineSeries  # pointsVector() is native in Qt5

    SparrowChartView = QChartView
    SparrowValueAxis = QValueAxis
    CHART_BACKEND = 'qtchart'

elif HAS_PYQTGRAPH:
    # pyqtgraph emulation of the QChart API subset that sparrow-wifi uses.
    # Widget classes come from the sparrowqt shim so this works under both
    # PyQt5 and PyQt6.
    import pyqtgraph as pg
    from sparrowqt import QWidget, QVBoxLayout, QTimer, QPointF, QRectF
    from sparrowqt import QBrush, QColor, QFont

    CHART_BACKEND = 'pyqtgraph'

    class SparrowValueAxis:
        """Emulates QValueAxis: setMin/setMax/setTickCount/setLabelFormat/setTitleText."""
        def __init__(self):
            self._min = 0
            self._max = 100
            self._tickCount = 5
            self._labelFormat = '%d'
            self._titleText = ''

        def setMin(self, v): self._min = v
        def setMax(self, v): self._max = v
        def setTickCount(self, n): self._tickCount = n
        def setLabelFormat(self, f): self._labelFormat = f
        def setTitleText(self, t): self._titleText = t

        # Styling no-ops: pyqtgraph styles axes through its own theme.
        def setLabelsColor(self, c): pass
        def setLabelsFont(self, f): pass
        def setLabelsVisible(self, b): pass
        def setTitleBrush(self, b): pass
        def setTitleFont(self, f): pass
        def setLinePen(self, p): pass
        def setLinePenColor(self, c): pass
        def setGridLinePen(self, p): pass
        def setGridLineColor(self, c): pass
        def setGridLineVisible(self, b): pass
        def setShadesBrush(self, b): pass
        def setShadesPen(self, p): pass
        def setShadesVisible(self, b): pass
        def setReverse(self, b): pass

        def setRange(self, minval, maxval):
            self._min = minval
            self._max = maxval

        def min(self): return self._min
        def max(self): return self._max
        def tickCount(self): return self._tickCount
        def labelFormat(self): return self._labelFormat
        def titleText(self): return self._titleText

    class SparrowLineSeries:
        """Emulates QLineSeries: append(name, x, y), replace(), clear(), setName(), pen()."""
        def __init__(self, parent=None):
            self._name = ''
            self._points = []  # list of (x, y)
            self._pen = None
            self.parentChart = None
            self._callbacks = {}  # not used in pyqtgraph mode

        def setName(self, n):
            self._name = n

        def name(self):
            return self._name

        def append(self, *args):
            if len(args) == 1:
                pt = args[0]
                if isinstance(pt, (tuple, list)):
                    self._points.append((float(pt[0]), float(pt[1])))
                elif hasattr(pt, 'x') and hasattr(pt, 'y'):
                    self._points.append((pt.x(), pt.y()))
            elif len(args) == 2:
                self._points.append((float(args[0]), float(args[1])))

        def replace(self, *args):
            """QLineSeries.replace overloads:
               replace(QList[QPointF]) / replace(points-list)
               replace(index, newX, newY)   - replace point at index
               replace(oldX, oldY, newX, newY) - find point, replace it
            """
            if len(args) == 1 and isinstance(args[0], (list, tuple)):
                self._points = []
                for pt in args[0]:
                    self.append(pt)
            elif len(args) == 3 and all(isinstance(a, (int, float)) for a in args):
                index, newx, newy = args
                if 0 <= index < len(self._points):
                    self._points[index] = (float(newx), float(newy))
            elif len(args) == 4:
                oldx, oldy, newx, newy = args
                target = (float(oldx), float(oldy))
                if target in self._points:
                    idx = self._points.index(target)
                    self._points[idx] = (float(newx), float(newy))

        def clear(self):
            self._points = []

        def setPen(self, pen):
            self._pen = pen

        def pen(self):
            return self._pen

        def pointsVector(self):
            # QLineSeries returns QPointF objects; callers use .x()/.y()
            return [QPointF(x, y) for (x, y) in self._points]

        def at(self, i):
            x, y = self._points[i]
            return QPointF(x, y)

        def count(self):
            return len(self._points)

        def remove(self, *args):
            """QLineSeries.remove(index) / remove(x, y) - index form is used
            by telemetry's rolling time-series."""
            if len(args) == 1 and isinstance(args[0], int):
                if 0 <= args[0] < len(self._points):
                    self._points.pop(args[0])
            elif len(args) == 2:
                target = (float(args[0]), float(args[1]))
                if target in self._points:
                    self._points.remove(target)

        # Signal stubs - pyqtgraph doesn't need these
        @property
        def hovered(self):
            class _Stub:
                def connect(self, *a): pass
            return _Stub()

        @property
        def clicked(self):
            class _Stub:
                def connect(self, *a): pass
            return _Stub()

        def attachAxis(self, axis):
            """No-op in pyqtgraph mode; the chart handles axis configuration."""
            pass

    class SparrowChart:
        """Emulates QChart: holds title, axes, series. The view is a pyqtgraph PlotWidget."""
        def __init__(self, *args, **kwargs):
            self._title = ''
            self._titleFont = QFont()
            self._titleBrush = QBrush(QColor(0, 0, 255))
            self._series = []
            self._axes = {}  # key -> SparrowValueAxis
            self._legendHidden = False
            self._plotWidget = None  # created lazily by SparrowChartView

        def setTitle(self, t):
            self._title = t
            if self._plotWidget:
                self._plotWidget.setLabel('top', t)

        def setTitleFont(self, f):
            self._titleFont = f

        def setTitleBrush(self, b):
            self._titleBrush = b

        def legend(self):
            class _Legend:
                def hide(self): pass
                def setVisible(self, b): pass
            return _Legend()

        def addAxis(self, axis, alignment):
            # Store the axis; the view will configure pyqtgraph's axes
            key = 'x' if 'Bottom' in str(alignment) else 'y'
            self._axes[key] = axis

        def addSeries(self, series):
            series.parentChart = self
            self._series.append(series)

        def removeSeries(self, series):
            if series in self._series:
                self._series.remove(series)
            if self._plotWidget:
                # Remove the corresponding plot data item
                for item in list(self._plotWidget.items()):
                    if hasattr(item, '_sparrowSeries') and item._sparrowSeries is series:
                        self._plotWidget.removeItem(item)

        def series(self):
            return self._series

        def setAcceptHoverEvents(self, b):
            pass  # pyqtgraph handles this differently

        def setBackgroundBrush(self, brush):
            pass  # pyqtgraph background is configured globally

        def removeAllSeries(self):
            self._series = []
            if self._plotWidget:
                for item in list(self._plotWidget.items()):
                    if hasattr(item, '_sparrowSeries'):
                        self._plotWidget.removeItem(item)

        def createDefaultAxes(self):
            pass  # pyqtgraph auto-creates axes

        def mapToPosition(self, value, series=None):
            # Used by Callout for hover text; approximate mapping
            if self._plotWidget:
                if hasattr(value, 'x') and hasattr(value, 'y'):
                    return QPointF(value.x(), value.y())
            return QPointF(0, 0)

        def plotArea(self):
            if self._plotWidget:
                r = self._plotWidget.geometry()
                return QRectF(r.x(), r.y(), r.width(), r.height())
            return QRectF(0, 0, 800, 600)

        def axisX(self):
            """Returns the x-axis for attachAxis() compatibility."""
            return self._axes.get('x', SparrowValueAxis())

        def axisY(self):
            """Returns the y-axis for attachAxis() compatibility."""
            return self._axes.get('y', SparrowValueAxis())

        def update(self):
            """Push all series data to the pyqtgraph widget."""
            if not self._plotWidget:
                return

            for series in self._series:
                if not series._points:
                    continue

                # Find or create the plot data item for this series
                item = None
                for it in self._plotWidget.items():
                    if hasattr(it, '_sparrowSeries') and it._sparrowSeries is series:
                        item = it
                        break

                if item is None:
                    # Create new plot data item
                    pen = pg.mkPen('w')
                    if series._pen:
                        try:
                            c = series._pen.color()
                            pen = pg.mkPen(c.name(), width=1)
                        except Exception:
                            pass
                    item = self._plotWidget.plot(
                        [], [], pen=pen, name=series._name or ''
                    )
                    item._sparrowSeries = series

                # Update the data
                xs = [p[0] for p in series._points]
                ys = [p[1] for p in series._points]
                item.setData(xs, ys)

            # Apply axis ranges
            if 'x' in self._axes:
                ax = self._axes['x']
                self._plotWidget.setXRange(ax.min(), ax.max(), padding=0)
                if ax.titleText():
                    self._plotWidget.setLabel('bottom', ax.titleText())
            if 'y' in self._axes:
                ay = self._axes['y']
                self._plotWidget.setYRange(ay.min(), ay.max(), padding=0)
                if ay.titleText():
                    self._plotWidget.setLabel('left', ay.titleText())

    class SparrowChartView(QWidget):
        """Wraps pyqtgraph PlotWidget to look like QChartView."""
        def __init__(self, chart, parent=None):
            super().__init__(parent)
            self._chart = chart
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)

            # Configure pyqtgraph
            pg.setConfigOptions(antialias=True, background='k', foreground='w')

            self._plotWidget = pg.PlotWidget()
            layout.addWidget(self._plotWidget)

            # Connect chart to the widget
            chart._plotWidget = self._plotWidget

            # Style
            self._plotWidget.showGrid(x=True, y=True, alpha=0.3)
            if chart._title:
                self._plotWidget.setLabel('top', chart._title)

        def setBackgroundBrush(self, color):
            pass  # pyqtgraph uses its own background config

        def setViewportUpdateMode(self, mode):
            pass  # QChartView-only; pyqtgraph redraws itself

        def setRenderHint(self, hint):
            pass  # pyqtgraph handles rendering internally

        def setChart(self, chart):
            self._chart = chart
            chart._plotWidget = self._plotWidget

        def chart(self):
            return self._chart

else:
    raise ImportError(
        "Neither QtChart nor pyqtgraph is available. "
        "Install one of: pyqt5-charts (Kali/Debian), PyQtChart (pip), "
        "or pyqtgraph (pip / dev-python/pyqtgraph on Gentoo)."
    )
