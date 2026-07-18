"""Render the FitFileMaker app icon as a 1024x1024 PNG using QPainter."""
import sys

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)

OUT = sys.argv[1]
S = 1024
# Apple icon grid: 824px squircle centered in 1024 canvas.
MARGIN = 100
RECT = QRectF(MARGIN, MARGIN, S - 2 * MARGIN, S - 2 * MARGIN)
RADIUS = 185

app = QGuiApplication(sys.argv)

img = QImage(S, S, QImage.Format_ARGB32_Premultiplied)
img.fill(Qt.transparent)

p = QPainter(img)
p.setRenderHint(QPainter.Antialiasing)

squircle = QPainterPath()
squircle.addRoundedRect(RECT, RADIUS, RADIUS)

# Background: indigo gradient, brand accent #4A55C0.
grad = QLinearGradient(RECT.topLeft(), RECT.bottomRight())
grad.setColorAt(0.0, QColor("#5B67DB"))
grad.setColorAt(0.55, QColor("#4A55C0"))
grad.setColorAt(1.0, QColor("#333C96"))
p.fillPath(squircle, grad)

# Everything else clips to the squircle.
p.setClipPath(squircle)

# Subtle top sheen.
sheen = QLinearGradient(RECT.topLeft(), QPointF(RECT.left(), RECT.center().y()))
sheen.setColorAt(0.0, QColor(255, 255, 255, 34))
sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
p.fillPath(squircle, sheen)

# ECG/workout waveform across the middle.
cy = S * 0.54  # baseline slightly below center to balance the spike
x0, x1 = RECT.left() + 78, RECT.right() - 78
w = x1 - x0

def X(f):
    return x0 + f * w

wave = QPainterPath(QPointF(X(0.0), cy))
wave.lineTo(X(0.16), cy)
# small warm-up bump
wave.quadTo(QPointF(X(0.21), cy - S * 0.045), QPointF(X(0.26), cy))
wave.lineTo(X(0.34), cy)
# main spike: sharp up, sharp down past baseline, recover
wave.lineTo(X(0.44), cy - S * 0.21)
wave.lineTo(X(0.54), cy + S * 0.10)
wave.lineTo(X(0.60), cy)
wave.lineTo(X(0.68), cy)
# cool-down bump
wave.quadTo(QPointF(X(0.73), cy - S * 0.045), QPointF(X(0.78), cy))
wave.lineTo(X(1.0), cy)

# Glow pass, then crisp white line.
glow = QPen(QColor(30, 33, 48, 90), 96, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
p.setPen(glow)
p.setBrush(Qt.NoBrush)
p.drawPath(wave)

line = QPen(QColor("#FEFEFF"), 44, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
p.setPen(line)
p.drawPath(wave)

p.end()

assert img.save(OUT), f"failed to save {OUT}"
print("wrote", OUT)
