"""Cửa sổ xem ảnh gốc (ảnh chọn từ máy, trước khi nắn bằng SIFT)."""
import tkinter as tk
from tkinter import ttk
from image_viewer import ZoomCanvas
from rectify import draw_outline
from window_layout import center_window


class OriginalView(tk.Toplevel):
    def __init__(self, parent, image, info=None, title='Ảnh gốc — ảnh trước'):
        super().__init__(parent); self.title(title); self.geometry('980x760')
        self.image = image; self.info = info or {}
        self.outline = tk.BooleanVar(value='outline_original' in self.info)
        h, w = image.shape[:2]
        if 'outline_original' in self.info:
            text = (f'Ảnh gốc {w}×{h}px • Khung xanh: vùng bia hệ thống trích ra • {self.info.get("method", "")} • '
                    f'{self.info.get("inliers", 0)}/{self.info.get("matches", 0)} điểm khớp, '
                    f'sai số {self.info.get("rmse_px", 0):.2f}px')
        elif self.info.get('failed'):
            text = f'Ảnh gốc {w}×{h}px • Không nắn được: {self.info["failed"]} (đang dùng ảnh gốc để chấm)'
        else:
            text = f'Ảnh gốc {w}×{h}px'
        ttk.Label(self, text=text + ' | Cuộn: phóng to; kéo: di chuyển', wraplength=940).pack(anchor='w', padx=8, pady=4)
        self.zoom = ZoomCanvas(self); self.zoom.pack(fill='both', expand=True)
        bar = ttk.Frame(self); bar.pack(fill='x')
        ttk.Button(bar, text='Vừa khung', command=self.zoom.fit).pack(side='left', padx=8, pady=6)
        if 'outline_original' in self.info:
            ttk.Checkbutton(bar, text='Hiện khung bia đã nhận diện', variable=self.outline, command=self.render).pack(side='left', padx=8)
        ttk.Button(bar, text='Đóng', command=self.destroy).pack(side='right', padx=8)
        self.render(); center_window(self, parent)

    def render(self):
        im = draw_outline(self.image, self.info) if self.outline.get() else self.image
        self.zoom.set_image(im)
