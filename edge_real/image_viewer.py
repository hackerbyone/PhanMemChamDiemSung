"""Zoomable inspection in source and template coordinates."""
import tkinter as tk
from tkinter import ttk
import cv2
import numpy as np
from PIL import Image,ImageTk
from core import overlay,transform
from annotations import draw_markers,hole_labels,is_old,is_visible
from window_layout import center_window


class ZoomCanvas(ttk.Frame):
    def __init__(self,parent,on_click=None,on_circle=None):
        super().__init__(parent);self.image=None;self.scale=1.;self.on_click=on_click;self.press=None;self.initial_fit_pending=True
        self.on_circle=on_circle;self.circle_start=None;self.on_drag_start=None;self.on_move=None;self.moving=None
        self.canvas=tk.Canvas(self,background='#263442',highlightthickness=0)
        x=ttk.Scrollbar(self,orient='horizontal',command=self.canvas.xview);y=ttk.Scrollbar(self,command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=x.set,yscrollcommand=y.set)
        self.canvas.grid(row=0,column=0,sticky='nsew');y.grid(row=0,column=1,sticky='ns');x.grid(row=1,column=0,sticky='ew')
        self.rowconfigure(0,weight=1);self.columnconfigure(0,weight=1)
        self.canvas.bind('<MouseWheel>',self.wheel)
        self.canvas.bind('<Configure>',self.initial_fit)
        self.canvas.bind('<ButtonPress-1>',self.down);self.canvas.bind('<B1-Motion>',self.pan);self.canvas.bind('<ButtonRelease-1>',self.up)
        self.canvas.bind('<ButtonPress-3>',self.right_down);self.canvas.bind('<B3-Motion>',self.right_pan);self.canvas.bind('<ButtonRelease-3>',self.right_up)
    def set_image(self,image,fit=False):
        first=self.image is None;self.image=Image.fromarray(cv2.cvtColor(image,cv2.COLOR_BGR2RGB))
        if first or fit:self.fit()
        else:self.draw()
    def initial_fit(self,event):
        if self.initial_fit_pending and self.image is not None and event.width>100 and event.height>100:
            self.initial_fit_pending=False;self.fit()
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
    def wheel(self,e):
        if self.press is None:self.zoom(1.2 if e.delta>0 else 1/1.2,e.x,e.y)
    def zoom(self,factor,x=None,y=None):
        if self.image is None:return
        x=self.canvas.winfo_width()/2 if x is None else x;y=self.canvas.winfo_height()/2 if y is None else y
        px=self.canvas.canvasx(x)/self.scale;py=self.canvas.canvasy(y)/self.scale
        self.scale=max(.05,min(8.,6000/max(self.image.size),self.scale*factor));self.draw()
        w,h=(v*self.scale for v in self.image.size)
        self.canvas.xview_moveto(max(0,(px*self.scale-x)/w));self.canvas.yview_moveto(max(0,(py*self.scale-y)/h))
    def down(self,e):
        self.press=(e.x,e.y);self.canvas.scan_mark(e.x,e.y);self.circle_start=None;self.moving=None
        if self.on_circle and self.image is not None:
            x,y=self.canvas.canvasx(e.x)/self.scale,self.canvas.canvasy(e.y)/self.scale
            if 0<=x<self.image.width and 0<=y<self.image.height:
                self.circle_start=(x,y)
    def right_down(self,e):
        self.down(e)
        if self.circle_start is not None and self.on_drag_start:
            self.moving=self.on_drag_start(list(self.circle_start))
    def right_pan(self,e):
        if self.press is None:return
        if self.moving:self.pan(e)
        else:self.canvas.scan_dragto(e.x,e.y,gain=1)
    def right_up(self,e):
        if self.moving and self.press and np.hypot(e.x-self.press[0],e.y-self.press[1])>=5:
            center=self.moved_center(e);item=self.moving;self.cancel_circle()
            if self.on_move:self.on_move(item,center)
        else:self.cancel_circle()
    def moved_center(self,e):
        x,y=self.moving['point'];sx,sy=self.circle_start
        return [float(np.clip(x+self.canvas.canvasx(e.x)/self.scale-sx,0,self.image.width-1)),
                float(np.clip(y+self.canvas.canvasy(e.y)/self.scale-sy,0,self.image.height-1))]
    def circle_radius(self,e):
        x,y=self.circle_start
        radius=np.hypot(self.canvas.canvasx(e.x)/self.scale-x,self.canvas.canvasy(e.y)/self.scale-y)
        return float(min(radius,x,y,self.image.width-1-x,self.image.height-1-y))
    def pan(self,e):
        if self.on_circle:
            if self.circle_start is None:return
            x,y=self.moved_center(e) if self.moving else self.circle_start
            r=self.moving.get('radius',12) if self.moving else self.circle_radius(e);s=self.scale
            color='#ff0000' if self.moving and self.moving['old'] else '#00cc44'
            self.canvas.delete('draft_circle');self.canvas.create_oval((x-r)*s,(y-r)*s,(x+r)*s,(y+r)*s,outline=color,width=2,tags='draft_circle')
        else:self.canvas.scan_dragto(e.x,e.y,gain=1)
    def up(self,e):
        if self.on_circle and self.circle_start and self.press and np.hypot(e.x-self.press[0],e.y-self.press[1])>=5:
            if self.moving:
                center=self.moved_center(e);item=self.moving;self.cancel_circle()
                if self.on_move:self.on_move(item,center)
                return
            center=self.circle_start;radius=self.circle_radius(e);self.cancel_circle()
            if radius*self.scale>=5:self.on_circle(list(center),radius)
            return
        if self.press and abs(e.x-self.press[0])+abs(e.y-self.press[1])<5 and self.on_click and self.image is not None:
            p=[self.canvas.canvasx(e.x)/self.scale,self.canvas.canvasy(e.y)/self.scale]
            if 0<=p[0]<self.image.width and 0<=p[1]<self.image.height:self.on_click(p)
        self.cancel_circle()
    def cancel_circle(self):
        self.press=None;self.circle_start=None;self.moving=None;self.canvas.delete('draft_circle')


class ImagePreview(tk.Toplevel):
    def __init__(self,app,kind,editable=False):
        super().__init__(app);self.app=app;self.kind=kind;self.editable=editable
        self.title('Ảnh '+('TRƯỚC — lỗ cũ' if kind=='before' else 'SAU — lỗ cũ & mới')+(' (chỉ xem)' if not editable else ''));self.geometry('1000x800')
        self.edit_index=None;self.hole_dialog=None;self.hint=tk.StringVar()
        if kind=='after' and editable:
            self.default_hint='Giữ chuột PHẢI trên vòng và kéo: dời vòng; thả để tính lại điểm.\nChuột TRÁI: bấm lỗ để đổi màu / xóa, kéo để vẽ vòng mới. Chuột phải ở vùng trống: kéo ảnh. Cuộn: zoom. Escape: hủy kéo.'
        elif kind=='after':
            self.default_hint='Đỏ: lỗ cũ • Xanh: lỗ mới • Cuộn: zoom • Kéo: di chuyển • Bấm lỗ để đối chiếu (chỉ xem; chỉnh sửa kết quả tại giao diện web)'
        else:
            self.default_hint='Lỗ cũ màu đỏ • Cuộn: zoom • Kéo: di chuyển (chỉ xem)'
        self.hint.set(self.default_hint);ttk.Label(self,textvariable=self.hint,wraplength=950).pack(padx=10,pady=8)
        self.zoom=ZoomCanvas(self,self.select_hole,self.save_circle if (kind=='after' and editable) else None);self.zoom.pack(fill='both',expand=True,padx=8)
        if kind=='after' and editable:self.zoom.on_drag_start=self.drag_start;self.zoom.on_move=self.move_circle
        self.bind('<Escape>',lambda e:self.cancel_edit())
        self.totals=tk.StringVar();ttk.Label(self,textvariable=self.totals).pack(pady=4)
        bar=ttk.Frame(self);bar.pack(pady=8)
        for label,cmd in [('−',lambda:self.zoom.zoom(1/1.2)),('+',lambda:self.zoom.zoom(1.2)),('Vừa khung',self.zoom.fit)]:ttk.Button(bar,text=label,command=cmd).pack(side='left',padx=4)
        self.render(True)
        center_window(self,app);self.zoom.fit()
    def cancel_edit(self):
        self.edit_index=None;self.zoom.cancel_circle();self.hint.set(self.default_hint)
    def save_circle(self,center,radius):
        def save():
            self.app.set_manual_hole(center,self.edit_index,radius=radius);self.cancel_edit()
        self.app.guard(save)
    def drag_start(self,point):
        if self.app.busy or self.edit_index is not None:return None
        return self.hit_hole(point)
    def move_circle(self,item,center):
        self.app.guard(lambda:self.app.set_manual_hole(center,item['index'],radius=item.get('radius',12)))
    def hit_hole(self,point):
        items=self.app.image_items(self.kind)
        if not items:return None
        distances=[np.linalg.norm(np.array(item['point'])-point) for item in items];index=int(np.argmin(distances))
        return items[index] if distances[index]<=max(items[index].get('radius',12),18/self.zoom.scale) else None
    def render(self,fit=False):
        image=getattr(self.app,self.kind+'_image')
        if image is not None:self.zoom.set_image(draw_markers(image,self.app.image_items(self.kind),self.app.image_selected(self.kind)),fit)
        if self.app.result:
            holes=self.app.result['detections'];self.totals.set(f"{sum(bool(h['selected']) for h in holes)} lỗ được giữ • Tổng {sum(h['score'] for h in holes if h['selected'])} điểm")
    def select_hole(self,point):
        if self.app.busy:return
        item=self.hit_hole(point)
        if item is None:return
        row=item['index']
        if self.kind=='before' and self.app.result:
            row=next((i for i,h in enumerate(self.app.result['detections']) if h.get('matched_before')==row),None)
        if row is not None and self.app.tree.exists(str(row)):
            self.app.tree.selection_set(str(row));self.app.tree.see(str(row));self.app.selection_changed()
            if self.kind=='after' and self.editable:self.open_hole_dialog(row)
    def open_hole_dialog(self,index):
        if self.hole_dialog and self.hole_dialog.winfo_exists():self.hole_dialog.destroy()
        self.cancel_edit();hole=self.app.result['detections'][index];label=hole_labels(self.app.result['detections'])[index]
        dialog=tk.Toplevel(self);self.hole_dialog=dialog;dialog.title('Sửa / xóa điểm '+label);dialog.transient(self);dialog.resizable(False,False)
        ttk.Label(dialog,text=f'{label} — {hole["score"]} điểm\nBạn muốn xóa điểm này hay vẽ lại vị trí?',padding=18).pack()
        colors=ttk.LabelFrame(dialog,text='Đổi màu / loại lỗ',padding=10);colors.pack(fill='x',padx=12)
        ttk.Label(colors,text='Đỏ: lỗ cũ, không cộng điểm. Xanh: lỗ mới, cộng điểm vùng.').pack()
        def change_color(kind):
            self.app.classify_hole(index,kind);dialog.destroy()
        for title,kind in [('Đỏ — Lỗ cũ','old'),('Xanh — Lỗ mới','new')]:
            ttk.Button(colors,text=title,command=lambda k=kind:self.app.guard(lambda:change_color(k))).pack(side='left',padx=5,pady=6)
        bar=ttk.Frame(dialog,padding=12);bar.pack()
        def remove():
            self.app.remove_hole(index);dialog.destroy()
        def redraw():
            self.edit_index=index;self.hint.set(f'Vẽ lại {label}: kéo chuột trái từ tâm mới ra mép vòng. Escape để hủy.');dialog.destroy()
        ttk.Button(bar,text='Xóa điểm này',command=lambda:self.app.guard(remove)).pack(side='left',padx=4)
        ttk.Button(bar,text='Vẽ lại vòng tròn',command=redraw).pack(side='left',padx=4)
        ttk.Button(bar,text='Hủy',command=dialog.destroy).pack(side='left',padx=4);center_window(dialog,self);dialog.grab_set()


class Inspection(tk.Toplevel):
    def __init__(self,app):
        super().__init__(app);self.app=app;self.title('Kiểm tra ảnh và từng lỗ');self.geometry('1100x780')
        self.mode=tk.StringVar(value='Ảnh sau nguyên gốc');self.rings=tk.BooleanVar(value=False);self.points=tk.BooleanVar(value=True)
        bar=ttk.Frame(self);bar.pack(fill='x',padx=8,pady=6)
        modes=ttk.Combobox(bar,textvariable=self.mode,state='readonly',width=27,values=['Ảnh trước nguyên gốc','Ảnh sau nguyên gốc','Ảnh sau đã căn','Bia mẫu']);modes.pack(side='left');modes.bind('<<ComboboxSelected>>',lambda e:self.render(True))
        for title,var in [('Vòng điểm / đường bao',self.rings),('Tâm và số lỗ',self.points)]:ttk.Checkbutton(bar,text=title,variable=var,command=self.render).pack(side='left',padx=8)
        self.zoom=ZoomCanvas(self,self.click);self.zoom.pack(fill='both',expand=True,padx=8)
        actions=ttk.Frame(self);actions.pack(fill='x',padx=8,pady=5)
        for title,cmd in [('−',lambda:self.zoom.zoom(1/1.2)),('+',lambda:self.zoom.zoom(1.2)),('Vừa khung',self.zoom.fit),('Sửa tâm / thêm lỗ',app.edit_hole),('Tính / bỏ lỗ',app.toggle),('Xác nhận kiểm tra',app.review)]:
            ttk.Button(actions,text=title,command=lambda f=cmd:app.guard(f)).pack(side='left',padx=3)
        ttk.Label(self,text='Cuộn chuột: phóng to/thu nhỏ • Kéo chuột: di chuyển • Nhấp lỗ hoặc chọn dòng để làm nổi lỗ').pack(anchor='w',padx=10)
        self.table=ttk.Treeview(self,columns=('number','xy','score','conf','selected','issue'),show='headings',height=5)
        for key,title,width in [('number','Số lỗ',65),('xy','Tâm trên mẫu',130),('score','Điểm',60),('conf','Tin cậy',70),('selected','Tính điểm',80),('issue','Đối chiếu / lưu ý',500)]:self.table.heading(key,text=title);self.table.column(key,width=width)
        self.table.pack(fill='x',padx=8,pady=6);self.table.bind('<<TreeviewSelect>>',self.select)
        self.table.bind('<Escape>',self.clear_selection)
        self.update_data()
        center_window(self,app);self.zoom.fit()
    def update_data(self):
        if not self.app.result:return
        selected=self.app.tree.selection();self.table.delete(*self.table.get_children())
        labels=hole_labels(self.app.result['detections'])
        for i,h in enumerate(self.app.result['detections']):
            self.table.insert('','end',iid=str(i),values=(labels[i],', '.join(f'{v:.1f}' for v in h['template_point']),h['score'] if h['selected'] else 0,'Thủ công' if h.get('source')=='manual' else f"{h.get('confidence',0):.0%}",'Có' if h['selected'] else 'Bỏ',('Lỗ cũ' if h.get('matched_before') is not None else 'Mới')+'; '+'; '.join(h.get('issues',[]))))
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
        self.display_indices=list(range(len(r['detections'])))
        if mode=='Ảnh trước nguyên gốc':
            items=self.app.image_items('before');self.display_points=[item['point'] for item in items]
            self.display_indices=[next((i for i,h in enumerate(r['detections']) if h.get('matched_before')==item['index']),None) for item in items]
            if self.points.get():image=draw_markers(image,items,self.app.image_selected('before'))
            self.zoom.set_image(image,fit);return
        if self.rings.get():
            if H is None:image=overlay(image,r['config'])
            elif 'score_map' in r['config']:
                from score_map import get_map
                labels=get_map(r['config'])
                for value in range(5,11):
                    contours,_=cv2.findContours((labels>=value).astype(np.uint8),cv2.RETR_LIST,cv2.CHAIN_APPROX_SIMPLE)
                    for contour in contours:
                        projected=np.array(transform(contour.reshape(-1,2),H),np.int32)
                        cv2.polylines(image,[projected],True,(0,210,255) if value>5 else (255,160,0),1)
            else:
                config=r['config'];outline=transform(config['outline'],H)
                outline=np.array(outline,np.int32);mask=np.zeros(image.shape[:2],np.uint8);cv2.fillPoly(mask,[outline],255);rings=image.copy()
                for radius in config['radii'].values():
                    theta=np.linspace(0,2*np.pi,241);center=np.array(config['center'])
                    circle=np.column_stack((np.cos(theta),np.sin(theta)))*radius+center
                    cv2.polylines(rings,[np.array(transform(circle,H),np.int32)],True,(0,210,255),1)
                image[mask>0]=rings[mask>0];cv2.polylines(image,[outline],True,(255,160,0),2)
        selected=self.app.tree.selection()
        self.display_indices=[]
        for i,h in enumerate(r['detections']):
            if not h['selected'] and (h.get('matched_before') is None or h.get('review_edited')):continue
            self.display_indices.append(i)
            p=h['template_point'] if H is None else transform([h['template_point']],H)[0];self.display_points.append(p)
            if self.points.get():
                center=tuple(round(v) for v in p);active=str(i) in selected;color=(0,0,255) if h.get('matched_before') is not None else (0,190,0)
                cv2.circle(image,center,15 if active else 7,color,3 if active else 2)
                label=hole_labels(r['detections'])[i]+(f":{h['score']}" if h.get('matched_before') is None else '')
                cv2.putText(image,label,(center[0]+9,center[1]),cv2.FONT_HERSHEY_SIMPLEX,.6,color,2)
        self.zoom.set_image(image,fit)
    def click(self,p):
        if not self.display_points:return
        distances=np.linalg.norm(np.array(self.display_points)-p,axis=1);i=int(distances.argmin())
        row=self.display_indices[i]
        if distances[i]<=max(12,18/self.zoom.scale) and row is not None:self.table.selection_set(str(row));self.table.see(str(row));self.select()
        else:self.clear_selection()
