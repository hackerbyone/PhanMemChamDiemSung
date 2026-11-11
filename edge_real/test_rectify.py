"""Kiểm tra nắn ảnh SIFT trên ảnh giả lập chụp nghiêng, có cảnh vật xung quanh."""
import copy
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from core import read_image
from rectify import rectify, draw_outline, required_mask
from storage import Store
from template_geometry import load_or_extract

ROOT = Path(__file__).resolve().parents[1]


def fake_photo(target, size=(3000, 2200), quad=None, seed=0, brightness=.8):
    """Đặt tờ bia lên nền lộn xộn theo phối cảnh nghiêng; trả về ảnh và H thật (ảnh -> mẫu)."""
    rng = np.random.default_rng(seed)
    w, h = size
    bg = cv2.resize(rng.integers(0, 255, (h // 40, w // 40, 3), dtype=np.uint8), (w, h), interpolation=cv2.INTER_LINEAR)
    for _ in range(40):  # "cảnh vật": hình khối ngẫu nhiên
        c = tuple(int(v) for v in rng.integers(0, 255, 3))
        x, y = int(rng.integers(0, w)), int(rng.integers(0, h))
        cv2.rectangle(bg, (x, y), (x + int(rng.integers(50, 400)), y + int(rng.integers(50, 400))), c, -1)
    th, tw = target.shape[:2]
    if quad is None:
        quad = [[700, 380], [2350, 520], [2250, 2050], [620, 1900]]
    dst = np.float32(quad)
    src = np.float32([[0, 0], [tw - 1, 0], [tw - 1, th - 1], [0, th - 1]])
    M = cv2.getPerspectiveTransform(src, dst)
    paper = cv2.warpPerspective(target, M, (w, h))
    mask = cv2.warpPerspective(np.full((th, tw), 255, np.uint8), M, (w, h))
    img = bg.copy(); img[mask > 0] = paper[mask > 0]
    # ánh sáng không đều + nhiễu
    yy, xx = np.mgrid[0:h, 0:w]
    light = brightness + .35 * (xx / w)
    img = np.clip(img * light[..., None] + rng.normal(0, 4, img.shape), 0, 255).astype(np.uint8)
    return img, np.linalg.inv(M)


def region_error(H, H_true, need):
    """Sai số (pixel ảnh mẫu) trên các điểm thuộc vùng tính điểm: chiếu điểm thật qua H ước lượng."""
    ys, xs = np.nonzero(need)
    pts = np.float32(np.c_[xs, ys][::300]).reshape(-1, 1, 2)
    src = cv2.perspectiveTransform(pts, np.linalg.inv(H_true))
    return np.linalg.norm(cv2.perspectiveTransform(src, H) - pts, axis=2)


class RectifyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = read_image(ROOT / 'assets/Biaso4.png')
        cls.after = read_image(ROOT / 'assets/2.Ket_thuc_New68.bmp')
        cls.config = load_or_extract(ROOT / 'assets/Biaso4.png', ROOT / 'target_config.json')

    def check(self, target, base=None, **kw):
        photo, H_true = fake_photo(target, **kw)
        if base is not None:  # ảnh bia vốn lệch so với mẫu: H thật = (bia -> mẫu) @ (ảnh -> bia)
            H_true = base @ H_true
        t = time.perf_counter()
        out, info = rectify(photo, self.template, self.config)
        info['seconds'] = time.perf_counter() - t
        self.assertEqual(out.shape, self.template.shape)
        err = region_error(np.array(info['homography']), H_true, required_mask(self.config, self.template.shape))
        self.assertLess(err.mean(), 1.0, info)
        self.assertLess(err.max(), 2.0, info)
        return photo, out, info, err

    def test_skewed_template(self):
        self.check(self.template)

    def test_skewed_shot_target_rotated(self):
        # bia có lỗ đạn, xoay ~ 90 độ và nghiêng mạnh. Ảnh này vốn lệch nhẹ so với mẫu nên lấy kết quả
        # nắn ảnh thẳng làm chuẩn: kiểm tra kết quả không đổi khi thêm nghiêng/xoay/nền/ánh sáng.
        _, info = rectify(self.after, self.template, self.config)
        base = np.array(info['homography'])
        self.check(self.after, base, quad=[[2300, 300], [2500, 1900], [800, 2050], [600, 450]], seed=3, brightness=.6)

    def test_missing_part_rejected(self):
        photo, _ = fake_photo(self.template, quad=[[-600, 200], [1100, 300], [1050, 1900], [-650, 1800]])
        with self.assertRaises(ValueError):
            rectify(photo, self.template, self.config)

    def test_unrelated_image_rejected(self):
        rng = np.random.default_rng(1)
        noise = cv2.GaussianBlur(rng.integers(0, 255, (1200, 1600, 3), dtype=np.uint8), (5, 5), 0)
        with self.assertRaises(ValueError):
            rectify(noise, self.template, self.config)

    def test_two_skewed_photos_same_holes(self):
        """Hai ảnh chụp nghiêng khác nhau của CÙNG một tờ bia: không được sinh lỗ mới có điểm."""
        from core import analyze, Detector
        a, _ = fake_photo(self.after, quad=[[2300, 300], [2500, 1900], [800, 2050], [600, 450]], seed=3, brightness=.6)
        b, _ = fake_photo(self.after, quad=[[500, 250], [2400, 400], [2350, 2100], [450, 1950]], seed=7, brightness=.9)
        (ra, ia), (rb, ib) = rectify(a, self.template, self.config), rectify(b, self.template, self.config)
        r = analyze(ra, rb, self.template, self.config, Detector(ROOT / 'assets/best.pt'), prealigned=(ia, ib))
        self.assertGreaterEqual(len(r['before_detections']), 8)
        self.assertEqual([h for h in r['detections'] if h['selected'] and h['score'] > 0], [])
        self.assertEqual(r['homography_before'], np.eye(3).tolist())


@unittest.skipUnless(os.environ.get('RUN_DESKTOP_TESTS') == '1', 'Set RUN_DESKTOP_TESTS=1')
class OpenBeforeFlow(unittest.TestCase):
    """Nút "Mở ảnh trước/sau": nắn ảnh, lưu ảnh gốc; nút "Xem ảnh gốc" chọn ảnh trước hoặc sau."""
    def setUp(self):
        import app
        self.mod = app
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / 'sample_check'); self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'data')
        config = load_or_extract(ROOT / 'assets/Biaso4.png', Path(self.tmp.name) / 'config.json')
        self.ask = patch.object(app.messagebox, 'askyesno', return_value=False)
        for p in [patch.object(app, 'Store', return_value=self.store),
                  patch.object(app, 'load_or_extract', return_value=copy.deepcopy(config)),
                  patch.object(app.App, 'task', lambda instance, func, done, text='': done(func())),
                  patch.object(app.messagebox, 'showerror', side_effect=lambda *a, **kw: (_ for _ in ()).throw(AssertionError(a))),
                  self.ask]:
            mock = p.start(); self.addCleanup(p.stop)
        self.ask = mock
        self.app = app.App(); self.app.withdraw(); self.addCleanup(self.app.destroy)
        for k, v in {'shooter_code': 'T1', 'shooter': 'Test', 'unit': 'U'}.items(): self.app.vars[k].set(v)

    def open_before(self, image, name='photo.jpg'):
        path = Path(self.tmp.name) / name; cv2.imwrite(str(path), image)
        with patch.object(self.mod.filedialog, 'askopenfilename', return_value=str(path)):
            self.app.load('before')
        return path

    def test_rectified_before_and_original_view(self):
        template = read_image(ROOT / 'assets/Biaso4.png')
        photo, _ = fake_photo(template)
        with patch.object(self.mod.App, 'detect_before', lambda instance: None):
            self.open_before(photo)
        self.assertEqual(self.app.before_image.shape, template.shape)
        info = self.app.meta['before_rectification']
        self.assertGreaterEqual(info['inliers'], 15)
        self.assertTrue((self.store.root / 'drafts' / self.app.round_id / 'before_original.jpg').exists())
        self.assertEqual(self.app.original_button.cget('text'), 'Xem ảnh gốc')
        menu = self.app.original_menu()
        self.assertEqual(menu.entrycget(0, 'state'), 'normal')
        self.assertEqual(menu.entrycget(1, 'state'), 'disabled')  # chưa có ảnh sau
        self.app.before_original = None  # buộc đọc lại từ thư mục nháp
        menu.invoke(0)
        window = self.app.zoom_windows['original_before']; window.update()
        self.assertEqual(window.image.shape, photo.shape)
        with self.assertRaises(ValueError):
            self.app.show_original('after')
        self.app.clear_round()  # như khi bấm Loạt tiếp theo
        self.assertFalse(window.winfo_exists())
        self.assertIsNone(self.app.before_original)
        with self.assertRaises(ValueError):
            self.app.choose_original()

    def test_after_photo_is_rectified_too(self):
        template = read_image(ROOT / 'assets/Biaso4.png')
        with patch.object(self.mod.App, 'detect_before', lambda instance: None),              patch.object(self.mod.App, 'run_analysis', lambda instance: None):
            self.open_before(fake_photo(template)[0])
            after_photo = fake_photo(template, seed=5)[0]
            path = Path(self.tmp.name) / 'after.jpg'; cv2.imwrite(str(path), after_photo)
            with patch.object(self.mod.filedialog, 'askopenfilename', return_value=str(path)):
                self.app.load('after')
        self.assertEqual(self.app.after_image.shape, template.shape)
        self.assertIsNotNone(self.app.rectification('before'))
        self.assertIsNotNone(self.app.rectification('after'))
        self.assertTrue((self.store.root / 'drafts' / self.app.round_id / 'after_original.jpg').exists())
        menu = self.app.original_menu()
        self.assertEqual([menu.entrycget(i, 'state') for i in (0, 1)], ['normal', 'normal'])
        self.app.after_original = None  # buộc đọc lại từ thư mục nháp
        menu.invoke(1)
        window = self.app.zoom_windows['original_after']; window.update()
        self.assertEqual(window.image.shape, after_photo.shape)
        self.assertIn('sau', window.title())

    def test_unusable_photo_can_be_declined(self):
        noise = np.random.default_rng(2).integers(0, 255, (900, 1200, 3), dtype=np.uint8)
        self.open_before(noise)
        self.assertTrue(self.ask.called)
        self.assertIsNone(self.app.before_image)
        with self.assertRaises(ValueError):
            self.app.choose_original()


if __name__ == '__main__':
    import sys
    if '--save' in sys.argv:  # xuất ảnh minh họa vào sample_check/
        t = RectifyTests(); RectifyTests.setUpClass()
        photo, out, info, err = t.check(t.after, np.array(rectify(t.after, t.template, t.config)[1]['homography']), quad=[[2300, 300], [2500, 1900], [800, 2050], [600, 450]], seed=3, brightness=.6)
        out_dir = ROOT / 'sample_check'; out_dir.mkdir(exist_ok=True)
        cv2.imwrite(str(out_dir / 'rectify_input.jpg'), cv2.resize(draw_outline(photo, info), None, fx=.4, fy=.4))
        cv2.imwrite(str(out_dir / 'rectify_output.png'), out)
        print({k: v for k, v in info.items() if k not in ('homography', 'outline_original')}, 'err_mean_px', round(float(err.mean()), 3), 'err_max_px', round(float(err.max()), 3))
    else:
        unittest.main()
