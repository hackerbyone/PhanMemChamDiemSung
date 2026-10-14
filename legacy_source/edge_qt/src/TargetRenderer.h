#pragma once
#include <QByteArray>
#include <QImage>
#include <QString>
#include <QVector>

// One recorded hit on a target face, in millimetres from center (matches
// the x_mm/y_mm/score columns of the `shot` table).
struct Hit
{
    double xMm = 0;
    double yMm = 0;
    double score = 0;
};

// Renders the same simple "target face + every hit so far" visualization as
// edge.py's render_target_image() (Pillow) -- scoring rings, a crosshair,
// and one colored dot per hit (green >=8, orange >=5, red otherwise) -- but
// with QImage/QPainter instead of Pillow. Geometry and color thresholds are
// kept numerically identical; exact pixel output need not match Pillow's,
// only the same target-face look and hit-color meaning.
class TargetRenderer
{
public:
    // Must match TARGET_MM_RANGE / IMG_PX in edge.py, and the
    // scale_mm_per_px=0.5 baked into physical_target.calibration_data.
    static constexpr int kImagePx = 320;
    static constexpr double kMmRange = 225.0;

    // Renders the image, writes it to `pngPath` (creating parent
    // directories as needed) and also returns the raw PNG bytes so the
    // caller can base64-encode them for the sync payload without re-reading
    // the file back off disk.
    static QByteArray renderToFile(const QString &pngPath, const QVector<Hit> &hits);

private:
    static QImage render(const QVector<Hit> &hits);
};
