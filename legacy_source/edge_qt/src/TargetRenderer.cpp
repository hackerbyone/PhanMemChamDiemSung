#include "TargetRenderer.h"

#include <QBuffer>
#include <QDir>
#include <QFileInfo>
#include <QPainter>

QImage TargetRenderer::render(const QVector<Hit> &hits)
{
    const double size = kImagePx;
    const double scale = (size / 2.0 - 12.0) / kMmRange;
    const double cx = size / 2.0;
    const double cy = size / 2.0;

    QImage img(kImagePx, kImagePx, QImage::Format_RGB32);
    img.fill(Qt::white);
    QPainter p(&img);
    p.setRenderHint(QPainter::Antialiasing, true);

    // Scoring rings, outer (10) to inner (1) -- same shading formula as
    // edge.py: shade = 235 - i*10.
    const int rings = 10;
    for (int i = rings; i >= 1; --i) {
        const double rPx = (kMmRange * i / rings) * scale;
        const int shade = 235 - i * 10;
        QPen pen(QColor(shade, shade, shade));
        p.setPen(pen);
        p.setBrush(Qt::NoBrush);
        p.drawEllipse(QPointF(cx, cy), rPx, rPx);
    }

    // Crosshair.
    QPen crossPen(Qt::black);
    p.setPen(crossPen);
    p.drawLine(QPointF(cx - 7, cy), QPointF(cx + 7, cy));
    p.drawLine(QPointF(cx, cy - 7), QPointF(cx, cy + 7));

    // Every hit recorded so far on this target, most recent included.
    for (const Hit &h : hits) {
        const double px = cx + h.xMm * scale;
        const double py = cy - h.yMm * scale;
        const double r = 5.0;
        QColor color;
        if (h.score >= 8.0)
            color = QColor(0, 140, 0);
        else if (h.score >= 5.0)
            color = QColor(230, 140, 0);
        else
            color = QColor(200, 0, 0);
        p.setPen(QPen(Qt::black));
        p.setBrush(color);
        p.drawEllipse(QPointF(px, py), r, r);
    }

    p.end();
    return img;
}

QByteArray TargetRenderer::renderToFile(const QString &pngPath, const QVector<Hit> &hits)
{
    QImage img = render(hits);

    QFileInfo fi(pngPath);
    QDir().mkpath(fi.absolutePath());
    img.save(pngPath, "PNG");

    QByteArray bytes;
    QBuffer buffer(&bytes);
    buffer.open(QIODevice::WriteOnly);
    img.save(&buffer, "PNG");
    buffer.close();
    return bytes;
}
