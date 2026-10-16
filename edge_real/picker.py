import tkinter as tk
from tkinter import ttk,messagebox
import cv2
from image_viewer import ZoomCanvas

class Picker(tk.Toplevel):
    def __init__(self,parent,image,title,minimum=1,maximum=None,preview=None):
        super().__init__(parent);self.title(title); self.points=[];self.result=None;self.maximum=maximum;self.minimum=minimum
        self.im=image;self.preview=preview;self.geometry('940x740')
        ttk.Label(self,text=title+' | Nhấp: thêm điểm; cuộn: phóng to; kéo: di chuyển',wraplength=900).pack()
        self.zoom=ZoomCanvas(self,self.click);self.zoom.pack(fill='both',expand=True)
        bar=ttk.Frame(self);bar.pack(fill='x')
        for label,cmd in [('Bỏ điểm cuối',self.undo),('Vừa khung',self.zoom.fit),('Xác nhận',self.done)]:ttk.Button(bar,text=label,command=cmd).pack(side='left',padx=8,pady=6)
        self.draw();self.grab_set();self.wait_window()
    def draw(self):
        im=self.im.copy()
        for i,p in enumerate(self.points):
            cv2.circle(im,tuple(map(round,p)),5,(0,0,255),-1);cv2.putText(im,str(i+1),tuple(map(round,p)),cv2.FONT_HERSHEY_SIMPLEX,.6,(0,0,255),2)
        if self.preview: im=self.preview(im,self.points)
        self.zoom.set_image(im)
    def click(self,p):
        if self.maximum is None or len(self.points)<self.maximum:self.points.append(p);self.draw()
    def undo(self,e=None):
        if self.points:self.points.pop();self.draw()
    def done(self):
        if len(self.points)<self.minimum:messagebox.showerror('Thiếu điểm',f'Cần ít nhất {self.minimum} điểm',parent=self);return
        self.result=self.points;self.destroy()

