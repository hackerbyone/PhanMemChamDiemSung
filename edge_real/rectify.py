"""Tiền xử lý ảnh bia chụp thực tế -> "ảnh lý tưởng" trong hệ tọa độ ảnh mẫu.

Quy trình (SIFT + CLAHE + FLANN + RANSAC/MAGSAC + ECC + warpPerspective):
  1. Chuyển xám, cân bằng sáng cục bộ CLAHE để chịu được ánh sáng không đều / bóng đổ.
  2. Thu nhỏ ảnh quá lớn (ảnh điện thoại 12MP…) chỉ để dò đặc trưng, giữ ảnh gốc để nắn.
  3. SIFT tìm điểm đặc trưng trên ảnh chụp và ảnh mẫu Biaso4.png.
  4. FLANN (KD-tree) + Lowe ratio test lọc cặp tương ứng.
  5. MAGSAC++ (RANSAC cải tiến) ước lượng homography, loại cặp sai (cảnh vật xung quanh, lỗ đạn…).
  6. ECC tinh chỉnh homography theo cường độ ảnh (sub-pixel); chỉ nhận khi khớp tốt hơn.
  7. warpPerspective ảnh gốc về đúng khung ảnh mẫu: tự xoay thẳng, khử nghiêng/phối cảnh,
     cắt bỏ cảnh vật bên ngoài tờ bia. Vùng ảnh chụp không phủ tới được tô trắng.
  8. Kiểm tra ảnh có phủ đủ vùng tính điểm hay không.

Kết quả là ảnh cùng kích thước ảnh mẫu, nên mọi bước sau (YOLO, chấm điểm) chạy như với ảnh chuẩn.
"""
import hashlib
import cv2
import numpy as np

FEATURE_MAX_SIDE = 1600      # cạnh dài tối đa khi dò SIFT trên ảnh chụp
RATIO = .75                  # Lowe ratio test
RANSAC_PX = 4.0              # ngưỡng sai số reprojection (pixel ảnh mẫu)
MIN_INLIERS = 15
MIN_COVERAGE = .985          # giống ngưỡng trong core.analyze
FILL = (255, 255, 255)       # màu nền tô vùng ngoài ảnh chụp

_template_cache = {}


def _gray(image):
    g = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    return cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(g)


def _scale_to(image, max_side):
    h, w = image.shape[:2]
    s = min(1., max_side / max(h, w))
    if s < 1:
        image = cv2.resize(image, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
    return image, s


def _sift():
    return cv2.SIFT_create(nfeatures=8000, contrastThreshold=.03)


def _template_features(template):
    key = (template.shape, hashlib.md5(np.ascontiguousarray(template).data).hexdigest())
    if key not in _template_cache:
        gray = _gray(template)
        k, d = _sift().detectAndCompute(gray, None)
        if d is None or len(k) < MIN_INLIERS:
            raise ValueError('Ảnh mẫu không đủ đặc trưng SIFT')
        _template_cache.clear()
        _template_cache[key] = (k, d, gray)
    return _template_cache[key]


def _ncc(a, b, mask):
    m = mask > 0
    if m.sum() < 100:
        return -1.
    x = a[m].astype(np.float64); y = b[m].astype(np.float64)
    x -= x.mean(); y -= y.mean()
    den = np.sqrt((x * x).sum() * (y * y).sum())
    return float((x * y).sum() / den) if den > 0 else -1.


def _warp_gray(gray, H, size):
    warped = cv2.warpPerspective(gray, H, size, flags=cv2.INTER_LINEAR)
    valid = cv2.warpPerspective(np.full(gray.shape[:2], 255, np.uint8), H, size, flags=cv2.INTER_NEAREST)
    return warped, cv2.erode(valid, np.ones((5, 5), np.uint8))


def _ecc_refine(src_gray, H, tmpl_gray):
    """Tinh chỉnh H bằng ECC; trả về (H, ncc_trước, ncc_sau, dùng_ECC)."""
    th, tw = tmpl_gray.shape[:2]
    warped, valid = _warp_gray(src_gray, H, (tw, th))
    before = _ncc(warped, tmpl_gray, valid)
    try:
        W = np.eye(3, dtype=np.float32)
        criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 80, 1e-5)
        _, W = cv2.findTransformECC(tmpl_gray.astype(np.float32), warped.astype(np.float32), W,
                                    cv2.MOTION_HOMOGRAPHY, criteria, valid, 5)
        # W: tọa độ mẫu -> tọa độ ảnh đã nắn; ảnh gốc -> mẫu là inv(W) @ H
        refined = np.linalg.inv(W.astype(np.float64)) @ H
        refined /= refined[2, 2]
        corners = np.float32([[0, 0], [tw - 1, 0], [tw - 1, th - 1], [0, th - 1]]).reshape(-1, 1, 2)
        shift = np.abs(cv2.perspectiveTransform(corners, W.astype(np.float64)) - corners).max()
        if not np.isfinite(refined).all() or shift > .03 * max(tw, th):
            return H, before, before, False
        w2, v2 = _warp_gray(src_gray, refined, (tw, th))
        after = _ncc(w2, tmpl_gray, v2)
        if after > before:
            return refined, before, after, True
    except cv2.error:
        pass
    return H, before, before, False


def required_mask(config, shape):
    """Vùng bắt buộc phải có trong ảnh (vùng tính điểm), theo cấu hình bia."""
    h, w = shape[:2]
    if config is None:
        return None
    if 'score_map' in config:
        from score_map import get_map
        labels = get_map(config)
        if labels.shape == (h, w):
            return labels > 0
    if config.get('outline'):
        m = np.zeros((h, w), np.uint8)
        cv2.fillPoly(m, [np.array(config['outline'], np.int32)], 255)
        return m > 0
    return None


def rectify(image, template, config=None):
    """Nắn ảnh chụp thực tế về khung ảnh mẫu.

    Trả về (ảnh_đã_xử_lý, thông_tin). thông_tin['homography'] ánh xạ ảnh gốc -> ảnh đã xử lý,
    thông_tin['outline_original'] là tứ giác tờ bia trên ảnh gốc (để vẽ khi xem ảnh gốc).
    """
    if image is None or image.size == 0:
        raise ValueError('Ảnh rỗng')
    th, tw = template.shape[:2]
    kt, dt, tmpl_gray = _template_features(template)

    small, s = _scale_to(image, FEATURE_MAX_SIDE)
    small_gray = _gray(small)
    ks, ds = _sift().detectAndCompute(small_gray, None)
    if ds is None or len(ks) < MIN_INLIERS:
        raise ValueError('Ảnh quá mờ hoặc không có chi tiết; không tìm thấy bia trong ảnh')

    flann = cv2.FlannBasedMatcher(dict(algorithm=1, trees=5), dict(checks=64))
    matches = flann.knnMatch(ds.astype(np.float32), dt.astype(np.float32), k=2)
    good = [m[0] for m in matches if len(m) == 2 and m[0].distance < RATIO * m[1].distance]
    if len(good) < MIN_INLIERS:
        raise ValueError(f'Chỉ tìm được {len(good)} điểm tương ứng với bia mẫu. Chụp rõ, đủ sáng và thấy toàn bộ bia.')
    p = np.float32([ks[m.queryIdx].pt for m in good])
    q = np.float32([kt[m.trainIdx].pt for m in good])
    method = cv2.USAC_MAGSAC if hasattr(cv2, 'USAC_MAGSAC') else cv2.RANSAC
    H, mask = cv2.findHomography(p, q, method, RANSAC_PX, maxIters=10000, confidence=.999)
    if H is None or mask is None or not np.isfinite(H).all() or abs(np.linalg.det(H)) < 1e-12:
        raise ValueError('Không tìm được phép nắn ảnh hợp lệ (homography)')
    use = mask.ravel().astype(bool)
    inliers = int(use.sum())
    if inliers < MIN_INLIERS:
        raise ValueError(f'Chỉ {inliers} điểm khớp tin cậy; ảnh có thể không phải bia này')
    proj = cv2.perspectiveTransform(p[use].reshape(-1, 1, 2), H).reshape(-1, 2)
    rmse = float(np.sqrt(np.mean(np.sum((proj - q[use]) ** 2, axis=1))))
    spread = abs(cv2.contourArea(cv2.convexHull(q[use]))) / (tw * th)
    if spread < .10:
        raise ValueError('Điểm khớp tập trung một chỗ; chụp lại thấy toàn bộ bia')

    # Tinh chỉnh ECC trên ảnh nhỏ (đơn vị: ảnh nhỏ -> mẫu)
    H, ncc_sift, ncc_final, used_ecc = _ecc_refine(small_gray, H, tmpl_gray)

    # H trên ảnh gốc: ảnh gốc -> ảnh nhỏ -> mẫu
    H_full = H @ np.diag([s, s, 1.])
    H_full /= H_full[2, 2]

    # Tứ giác tờ bia trên ảnh gốc, kiểm tra suy biến
    tcorners = np.float32([[0, 0], [tw - 1, 0], [tw - 1, th - 1], [0, th - 1]]).reshape(-1, 1, 2)
    quad = cv2.perspectiveTransform(tcorners, np.linalg.inv(H_full)).reshape(-1, 2)
    if not np.isfinite(quad).all() or not cv2.isContourConvex(quad.astype(np.float32)):
        raise ValueError('Phép nắn ảnh bị suy biến; chụp lại bia ít nghiêng hơn')
    quad_area = abs(cv2.contourArea(quad.astype(np.float32)))

    # Nếu tờ bia trên ảnh gốc lớn hơn nhiều so với ảnh mẫu, thu nhỏ trước (INTER_AREA) để tránh răng cưa
    src, H_warp = image, H_full
    shrink = np.sqrt(tw * th / max(quad_area, 1.))
    if shrink < .7:
        f = shrink / .7
        src = cv2.resize(image, (max(1, round(image.shape[1] * f)), max(1, round(image.shape[0] * f))), interpolation=cv2.INTER_AREA)
        H_warp = H_full @ np.diag([1 / f, 1 / f, 1.])
    rectified = cv2.warpPerspective(src, H_warp, (tw, th), flags=cv2.INTER_CUBIC,
                                    borderMode=cv2.BORDER_CONSTANT, borderValue=FILL)
    valid = cv2.warpPerspective(np.full(src.shape[:2], 255, np.uint8), H_warp, (tw, th), flags=cv2.INTER_NEAREST)

    need = required_mask(config, template.shape)
    coverage = float(np.mean(valid[need] > 0)) if need is not None and need.any() else float(np.mean(valid > 0))
    if coverage < MIN_COVERAGE:
        raise ValueError(f'Ảnh chỉ chứa {coverage:.0%} vùng tính điểm của bia. Chụp lại thấy toàn bộ hình người.')

    info = {
        'method': 'CLAHE+SIFT+FLANN+' + ('MAGSAC' if method != cv2.RANSAC else 'RANSAC') + ('+ECC' if used_ecc else ''),
        'homography': H_full.tolist(),
        'outline_original': quad.tolist(),
        'original_size': [int(image.shape[1]), int(image.shape[0])],
        'matches': len(good), 'inliers': inliers, 'inlier_ratio': inliers / len(good),
        'rmse_px': rmse, 'coverage': coverage,
        'ncc_sift': ncc_sift, 'ncc_final': ncc_final,
    }
    return rectified, info


def draw_outline(image, info, color=(0, 220, 0)):
    """Vẽ tứ giác vùng bia được trích ra lên ảnh gốc."""
    out = image.copy()
    quad = np.array(info['outline_original'], np.float32)
    thick = max(2, round(max(image.shape[:2]) / 400))
    cv2.polylines(out, [np.round(quad).astype(np.int32)], True, color, thick, cv2.LINE_AA)
    return out
