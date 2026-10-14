import tkinter as tk
from tkinter import ttk,messagebox
import cv2
from PIL import Image,ImageTk

class Picker(tk.Toplevel):
    def __init__(self,parent,image,title,minimum=1,maximum=None,preview=None):
        super().__init__(parent);self.title(title); self.points=[];self.result=None;self.maximum=maximum;self.minimum=minimum
        self.scale=min(1,800/image.shape[1],650/image.shape[0]);self.im=image;self.preview=preview
        ttk.Label(self,text=title+' | Nhấp trái: thêm; nhấp phải: bỏ điểm cuối',wraplength=780).pack()
        self.canvas=tk.Canvas(self,width=round(image.shape[1]*self.scale),height=round(image.shape[0]*self.scale));self.canvas.pack()
        self.canvas.bind('<Button-1>',self.click);self.canvas.bind('<Button-3>',self.undo)
        ttk.Button(self,text='Xác nhận',command=self.done).pack(); self.draw();self.grab_set();self.wait_window()
    def draw(self):
        im=self.im.copy()
        for i,p in enumerate(self.points):
            cv2.circle(im,tuple(map(round,p)),5,(0,0,255),-1);cv2.putText(im,str(i+1),tuple(map(round,p)),cv2.FONT_HERSHEY_SIMPLEX,.6,(0,0,255),2)
        if self.preview: im=self.preview(im,self.points)
        im=cv2.resize(im,None,fx=self.scale,fy=self.scale)
        self.photo=ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(im,cv2.COLOR_BGR2RGB)))
        self.canvas.delete('all');self.canvas.create_image(0,0,anchor='nw',image=self.photo)
    def click(self,e):
        if self.maximum is None or len(self.points)<self.maximum:self.points.append([e.x/self.scale,e.y/self.scale]);self.draw()
    def undo(self,e):
        if self.points:self.points.pop();self.draw()
    def done(self):
        if len(self.points)<self.minimum:messagebox.showerror('Thiếu điểm',f'Cần ít nhất {self.minimum} điểm',parent=self);return
        self.result=self.points;self.destroy()

