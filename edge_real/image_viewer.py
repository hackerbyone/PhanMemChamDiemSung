"""Zoomable inspection in source and template coordinates."""
import tkinter as tk
from tkinter import ttk
import cv2
import numpy as np
from PIL import Image,ImageTk
from core import overlay,transform


class ZoomCanvas(ttk.Frame):
    def __init__(self,parent,on_click=None):
        super().__init__(parent);self.image=None;self.scale=1.;self.on_click=on_click;self.press=None
        self.canvas=tk.Canvas(self,background='#263442',highlightthickness=0)
        x=ttk.Scrollbar(self,orient='horizontal',command=self.canvas.xview);y=ttk.Scrollbar(self,command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=x.set,yscrollcommand=y.set)
        self.canvas.grid(row=0,column=0,sticky='nsew');y.grid(row=0,column=1,sticky='ns');x.grid(row=1,column=0,sticky='ew')
        self.rowconfigure(0,weight=1);self.columnconfigure(0,weight=1)
        self.canvas.bind('<MouseWheel>',self.wheel)
        self.canvas.bind('<ButtonPress-1>',self.down);self.canvas.bind('<B1-Motion>',self.pan);self.canvas.bind('<ButtonRelease-1>',self.up)
        self.canvas.bind('<ButtonPress-3>',lambda e:self.canvas.scan_mark(e.x,e.y));self.canvas.bind('<B3-Motion>',lambda e:self.canvas.scan_dragto(e.x,e.y,gain=1))
    def set_image(self,image,fit=False):
        first=self.image is None;self.image=Image.fromarray(cv2.cvtColor(image,cv2.COLOR_BGR2RGB))
        if first or fit:self.fit()
        else:self.draw()
    def fit(self):
        if self.image is None:return
        self.update_idletasks()
        self.scale=min(1.,max(100,self.canvas.winfo_width()-4)/self.image.width,max(100,self.canvas.winfo_height()-4)/self.image.height)
        self.draw();self.canvas.xview_moveto(0);self.canvas.yview_moveto(0)
    def draw(self):
        if self.image is None:return
        size=tuple(max(1,round(v*self.scale)) for v in self.image.size)
        self.photo=ImageTk.PhotoImage(self.image.resize(size,Image.Resampling.LANCZOS))
        self.canvas.delete('all');self.canvas.create_image(0,0,image=self.photo,anchor='nw');self.canvas.configure(scrollregion=(0,0,*size))
    def wheel(self,e):self.zoom(1.2 if e.delta>0 else 1/1.2,e.x,e.y)
    def zoom(self,factor,x=None,y=None):
        if self.image is None:return
        x=self.canvas.winfo_width()/2 if x is None else x;y=self.canvas.winfo_height()/2 if y is None else y
        px=self.canvas.canvasx(x)/self.scale;py=self.canvas.canvasy(y)/self.scale
        self.scale=max(.05,min(8.,6000/max(self.image.size),self.scale*factor));self.draw()
        w,h=(v*self.scale for v in self.image.size)
        self.canvas.xview_moveto(max(0,(px*self.scale-x)/w));self.canvas.yview_moveto(max(0,(py*self.scale-y)/h))
    def down(self,e):self.press=(e.x,e.y);self.canvas.scan_mark(e.x,e.y)
    def pan(self,e):self.canvas.scan_dragto(e.x,e.y,gain=1)
    def up(self,e):
        if self.press and abs(e.x-self.press[0])+abs(e.y-self.press[1])<5 and self.on_click and self.image is not None:
            p=[self.canvas.canvasx(e.x)/self.scale,self.canvas.canvasy(e.y)/self.scale]
            if 0<=p[0]<self.image.width and 0<=p[1]<self.image.height:self.on_click(p)
        self.press=None


class Inspection(tk.Toplevel):
    def __init__(self,app):
        super().__init__(app);self.app=app;self.title('Kiểm tra ảnh và từng lỗ');self.geometry('1100x780')
        self.mode=tk.StringVar(value='Ảnh sau nguyên gốc');self.rings=tk.BooleanVar(value=True);self.points=tk.BooleanVar(value=True)
        bar=ttk.Frame(self);bar.pack(fill='x',padx=8,pady=6)
        modes=ttk.Combobox(bar,textvariable=self.mode,state='readonly',width=27,values=['Ảnh trước nguyên gốc','Ảnh sau nguyên gốc','Ảnh sau đã căn','Bia mẫu']);modes.pack(side='left');modes.bind('<<ComboboxSelected>>',lambda e:self.render(True))
        for title,var in [('Vòng điểm / đường bao',self.rings),('Tâm và số lỗ',self.points)]:ttk.Checkbutton(bar,text=title,variable=var,command=self.render).pack(side='left',padx=8)
        self.zoom=ZoomCanvas(self,self.click);self.zoom.pack(fill='both',expand=True,padx=8)
        actions=ttk.Frame(self);actions.pack(fill='x',padx=8,pady=5)
        for title,cmd in [('−',lambda:self.zoom.zoom(1/1.2)),('+',lambda:self.zoom.zoom(1.2)),('Vừa khung',self.zoom.fit),('Sửa tâm / thêm lỗ',app.edit_hole),('Tính / bỏ lỗ',app.toggle),('Xác nhận kiểm tra',app.review)]:
            ttk.Button(actions,text=title,command=lambda f=cmd:app.guard(f)).pack(side='left',padx=3)
        ttk.Label(self,text='Cuộn chuột: phóng to/thu nhỏ • Kéo chuột: di chuyển • Nhấp lỗ hoặc chọn dòng để làm nổi lỗ').pack(anchor='w',padx=10)
        self.table=ttk.Treeview(self,columns=('xy','score','selected','issue'),show='headings',height=5)
        for key,title,width in [('xy','Tâm trên mẫu',170),('score','Điểm',60),('selected','Tính điểm',80),('issue','Đối chiếu / lưu ý',650)]:self.table.heading(key,text=title);self.table.column(key,width=width)
        self.table.pack(fill='x',padx=8,pady=6);self.table.bind('<<TreeviewSelect>>',self.select)
        self.table.bind('<Escape>',self.clear_selection)
        self.update_data()
    def update_data(self):
        if not self.app.result:return
        selected=self.app.tree.selection();self.table.delete(*self.table.get_children())
        for i,h in enumerate(self.app.result['detections']):
            self.table.insert('','end',iid=str(i),values=(', '.join(f'{v:.1f}' for v in h['template_point']),h['score'],'Có' if h['selected'] else 'Bỏ',('Lỗ cũ' if h.get('matched_before') is not None else 'Mới')+'; '+'; '.join(h.get('issues',[]))))
        if selected and self.table.exists(selected[0]):self.table.selection_set(selected)
        self.render()
    def clear_selection(self,e=None):
        self.table.selection_remove(*self.table.selection());self.app.tree.selection_remove(*self.app.tree.selection());self.render()
    def select(self,e=None):
        selected=self.table.selection()
        if selected:self.app.tree.selection_set(selected);self.app.tree.see(selected[0])
        self.render()
    def render(self,fit=False):
        r=self.app.result
        if not r:return
        mode=self.mode.get();self.display_points=[]
        if mode=='Bia mẫu':image=self.app.template.copy();H=None
        elif mode=='Ảnh sau đã căn':image=cv2.warpPerspective(self.app.after_image,np.array(r['homography_after']),(self.app.template.shape[1],self.app.template.shape[0]));H=None
        else:
            kind='before' if mode=='Ảnh trước nguyên gốc' else 'after'
            image=getattr(self.app,kind+'_image').copy();H=np.linalg.inv(np.array(r['homography_'+kind]))
        if self.rings.get():
            if H is None:image=overlay(image,r['config'])
            else:
                config=r['config'];outline=transform(config['outline'],H)
                outline=np.array(outline,np.int32);mask=np.zeros(image.shape[:2],np.uint8);cv2.fillPoly(mask,[outline],255);rings=image.copy()
                for radius in config['radii'].values():
                    theta=np.linspace(0,2*np.pi,241);center=np.array(config['center'])
                    circle=np.column_stack((np.cos(theta),np.sin(theta)))*radius+center
                    cv2.polylines(rings,[np.array(transform(circle,H),np.int32)],True,(0,210,255),1)
                image[mask>0]=rings[mask>0];cv2.polylines(image,[outline],True,(255,160,0),2)
        selected=self.app.tree.selection()
        for i,h in enumerate(r['detections']):
            p=h['template_point'] if H is None else transform([h['template_point']],H)[0];self.display_points.append(p)
            if self.points.get():
                center=tuple(round(v) for v in p);active=str(i) in selected;color=(255,0,255) if active else ((0,200,0) if h['selected'] else (100,100,255))
                cv2.circle(image,center,15 if active else 7,color,3 if active else 2)
                cv2.putText(image,f"{i+1}:{h['score']}",(center[0]+9,center[1]),cv2.FONT_HERSHEY_SIMPLEX,.6,color,2)
        self.zoom.set_image(image,fit)
    def click(self,p):
        if not self.display_points:return
        distances=np.linalg.norm(np.array(self.display_points)-p,axis=1);i=int(distances.argmin())
        if distances[i]<=max(12,18/self.zoom.scale):self.table.selection_set(str(i));self.table.see(str(i));self.select()
        else:self.clear_selection()
