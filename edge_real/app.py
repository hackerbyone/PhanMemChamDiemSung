"""Automatic pair capture workflow. Advanced editing stays off the main path."""
import copy
import json
import os
import queue
import threading
import uuid
import webbrowser
from datetime import datetime,date
from pathlib import Path
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
import cv2
import numpy as np
from PIL import Image,ImageTk
from core import read_image,sha,validate_config,overlay,analyze,Detector,score_point,align,transform,prealigned_quality
from annotations import markers,draw_markers,hole_labels,is_old
from core import rescore_result
from storage import Store,utc,atomic_json,LOCAL_TZ
from template_geometry import load_or_extract
from score_map import attach_map
from picker import Picker
from management import Management
from shooter_queue import ShooterQueue
from image_viewer import Inspection,ImagePreview
import shutil
from rectify import rectify
from original_view import OriginalView
ROOT=Path(__file__).resolve().parents[1]

class App(ShooterQueue,Management,tk.Tk):
    def __init__(self):
        super().__init__();self.title('Chấm điểm bia — Tự động v2 / CPU');self.geometry(f'1280x{min(900,self.winfo_screenheight()-90)}');self.minsize(1050,680)
        self.template_path=ROOT/'assets/Biaso4.png';self.template=read_image(self.template_path);self.config_path=ROOT/'target_config.json';self.config=None;self.default_config=None
        self.before_image=None;self.after_image=None;self.result=None;self.busy=False;self.cap=None;self.frame=None
        self.detector=None;self.pairs=None;self.round_id=None;self.meta=None;self.saved=False;self.messages=queue.Queue();self.store=Store(ROOT/'data')
        self.expected_revision=None;self.inspector=None;self.recovery_offered=False;self.before_preview=[];self.zoom_windows={}
        self.vars={};self.identity_entries=[];self.photos=[];self.before_original=None;self.after_original=None
        style=ttk.Style();style.configure('Title.TLabel',font=('Segoe UI',17,'bold'));style.configure('TButton',padding=6)
        top_bar=ttk.Frame(self);top_bar.pack(fill='x',padx=16,pady=(12,3))
        ttk.Label(top_bar,text='Chấm điểm tự động từ hai ảnh',style='Title.TLabel').pack(side='left')
        self.queue_header=tk.StringVar(value='CHƯA CHỌN NGƯỜI BẮN')
        ttk.Label(top_bar,textvariable=self.queue_header,font=('Segoe UI',11,'bold'),foreground='#166534').pack(side='right',padx=8)
        self.notebook=ttk.Notebook(self);self.notebook.pack(fill='both',expand=True,padx=12,pady=8)
        main_tab=ttk.Frame(self.notebook);advanced=ttk.Frame(self.notebook);self.notebook.add(main_tab,text='Loạt bắn');self.notebook.add(advanced,text='Thiết lập & kiểm tra')
        main_tab.columnconfigure(1,weight=1);main_tab.rowconfigure(0,weight=1)
        sidebar=ttk.Frame(main_tab,width=285);sidebar.grid(row=0,column=0,sticky='ns',padx=(0,10));sidebar.grid_propagate(False)
        sidebar.rowconfigure(0,weight=1);sidebar.columnconfigure(0,weight=1)
        scroll=tk.Canvas(sidebar,highlightthickness=0,width=265);sb=ttk.Scrollbar(sidebar,orient='vertical',command=scroll.yview)
        scroll.configure(yscrollcommand=sb.set);scroll.grid(row=0,column=0,sticky='nsew');sb.grid(row=0,column=1,sticky='ns')
        controls=ttk.Frame(scroll);content=scroll.create_window((0,0),window=controls,anchor='nw')
        controls.bind('<Configure>',lambda e:scroll.configure(scrollregion=scroll.bbox('all')))
        scroll.bind('<Configure>',lambda e:scroll.itemconfigure(content,width=e.width))
        self.bind('<MouseWheel>',lambda e:scroll.yview_scroll(-int(e.delta/120),'units') if self.notebook.index('current')==0 else None)
        form=ttk.LabelFrame(controls,text='Người bắn & loạt bắn',padding=8);form.pack(fill='x',pady=(4,8))
        self.button(form,'Tìm / chọn người bắn',self.open_roster).grid(row=0,column=0,columnspan=2,sticky='ew',pady=(0,8))
        for row,(key,label,default) in enumerate([('shooter_code','Mã',''),('shooter','Họ tên',''),('unit','Đơn vị',''),('lane','Bệ bắn','1'),('series','Loạt','1'),('expected','Số phát',''),('shooting_date','Ngày bắn',datetime.now(LOCAL_TZ).date().isoformat()),('notes','Ghi chú','')],1):
            ttk.Label(form,text=label).grid(row=row,column=0,sticky='w',pady=3,padx=(0,6));v=tk.StringVar(value=default);self.vars[key]=v
            entry=ttk.Entry(form,textvariable=v,width=19);entry.grid(row=row,column=1,sticky='ew',pady=3);self.identity_entries.append(entry)
        form.columnconfigure(1,weight=1)
        actions=ttk.LabelFrame(controls,text='Điều khiển',padding=8);actions.pack(fill='x')
        for name,cmd in [('Mở / đóng camera',self.camera),('1. Chụp TRƯỚC',lambda:self.capture('before')),('2. Chụp SAU & tính điểm',lambda:self.capture('after')),('Mở ảnh trước',lambda:self.load('before')),('Mở ảnh sau & tính điểm',lambda:self.load('after')),('Xem ảnh gốc',self.choose_original),('Tính lại / thử lại',self.run_analysis),('Loạt tiếp theo',self.reset),('Người tiếp theo',self.queue_next_or_reset),('Thử hai ảnh mẫu',self.demo)]:
            b=self.button(actions,name,cmd);b.pack(fill='x',pady=2)
            if name=='Xem ảnh gốc':self.original_button=b
        main=ttk.Frame(main_tab);main.grid(row=0,column=1,sticky='nsew');main.columnconfigure(0,weight=1);main.rowconfigure(1,weight=1)
        ttk.Label(main,text='Đỏ: lỗ cũ • Xanh: lỗ mới • Bấm ảnh để zoom/sửa • Giữ chuột phải trên vòng để kéo, thả để tính lại điểm',wraplength=720).grid(row=0,column=0,sticky='w',pady=(8,6))
        panels=ttk.Frame(main);panels.grid(row=1,column=0,sticky='nsew');panels.rowconfigure(0,weight=1);self.views=[]
        for i,label in enumerate(['Ảnh TRƯỚC / camera','Ảnh SAU — cũ & mới']):
            panels.columnconfigure(i,weight=1,uniform='images');p=ttk.LabelFrame(panels,text=label,padding=3,width=300,height=350);p.grid(row=0,column=i,sticky='nsew',padx=3);p.pack_propagate(False)
            v=ttk.Label(p,text='Chưa có ảnh',anchor='center',cursor='hand2');v.pack(fill='both',expand=True);self.views.append(v)
            v.bind('<Button-1>',lambda e,k=('before' if i==0 else 'after'):self.guard(lambda:self.open_image(k)))
            v.bind('<Configure>',lambda e,w=v:self.resize_preview(w))
        self.summary=tk.StringVar(value='Đang nạp bản đồ điểm…');ttk.Label(main,textvariable=self.summary,font=('Segoe UI',11,'bold'),wraplength=760).grid(row=2,column=0,sticky='w',padx=5,pady=8)
        rows=ttk.Frame(main);rows.grid(row=3,column=0,sticky='ew');rows.columnconfigure(0,weight=1)
        self.tree=ttk.Treeview(rows,columns=('number','take','score','conf','status'),show='headings',height=7)
        for key,label,width in [('number','Số lỗ',65),('take','Tính điểm',75),('score','Điểm',55),('conf','Tin cậy',75),('status','Đối chiếu / lưu ý',330)]:
            self.tree.heading(key,text=label);self.tree.column(key,width=width,minwidth=50,stretch=key=='status')
        self.tree.grid(row=0,column=0,sticky='nsew');ys=ttk.Scrollbar(rows,command=self.tree.yview);ys.grid(row=0,column=1,sticky='ns');self.tree.configure(yscrollcommand=ys.set)
        self.tree.bind('<Escape>',lambda e:self.tree.selection_remove(*self.tree.selection()))
        self.tree.bind('<<TreeviewSelect>>',lambda e:self.selection_changed())
        self.tree.bind('<Double-1>',lambda e:self.guard(self.open_inspection))
        queue_box=ttk.Frame(main);queue_box.grid(row=4,column=0,sticky='ew');self.build_queue_panel(queue_box)
        bottom=ttk.Frame(main);bottom.grid(row=5,column=0,sticky='ew',pady=6)
        self.button(bottom,'Mở kết quả trên Server',lambda:webbrowser.open(self.url.get().rstrip('/')+'/real')).pack(side='right')
        self.url=tk.StringVar(value='http://127.0.0.1:8000');self.password=tk.StringVar();self.auto_sync=tk.BooleanVar(value=True)
        self.vars['camera']=tk.StringVar(value='0');self.vars['confidence']=tk.StringVar(value='.25');self.vars['radius']=tk.StringVar(value='12')
        settings=ttk.LabelFrame(advanced,text='Thiết lập một lần');settings.pack(fill='x',padx=8,pady=8)
        for row,(label,var) in enumerate([('Chỉ số camera USB',self.vars['camera']),('Ngưỡng YOLO',self.vars['confidence']),('Khoảng ghép lỗ (pixel mẫu)',self.vars['radius']),('Server',self.url),('Mật khẩu quản lý',self.password)]):
            ttk.Label(settings,text=label).grid(row=row,column=0,sticky='w',padx=8,pady=5);ttk.Entry(settings,textvariable=var,width=40,show='*' if row==4 else '').grid(row=row,column=1,sticky='w',padx=8)
        ttk.Checkbutton(settings,text='Tự gửi kết quả sau khi lưu (cần mật khẩu; mất mạng sẽ giữ hàng đợi)',variable=self.auto_sync).grid(row=5,columnspan=2,sticky='w',padx=8,pady=5)
        self.button(settings,'Gửi lại các loạt đang chờ',self.sync).grid(row=6,column=1,sticky='w',pady=6)
        tools=ttk.LabelFrame(advanced,text='Chỉ dùng khi cần kiểm tra hoặc sửa');tools.pack(fill='x',padx=8,pady=8)
        for row,(name,cmd) in enumerate([('Nạp lại bản đồ điểm',self.rebuild_template),('Căn chỉnh thủ công khi tự động thất bại',self.manual),('Xem hai ảnh chồng với mẫu',self.inspect),('Xác nhận đã kiểm tra kết quả',self.review)]):self.button(tools,name,cmd).grid(row=row,column=0,sticky='w',padx=8,pady=3)
        ttk.Label(tools,text='Bấm ảnh sau để sửa / xóa lỗ hoặc kéo vẽ vòng tròn thêm lỗ.\nĐiểm tự cập nhật sau mỗi thao tác.\nLỗ trúng chồng hoàn toàn không thể đếm chắc chắn từ hai ảnh.',wraplength=460).grid(row=0,column=1,rowspan=5,padx=15)
        self.status=tk.StringVar(value='Đang nạp bản đồ điểm…');ttk.Label(self,textvariable=self.status,wraplength=1100).pack(fill='x',padx=16,pady=(0,10))
        self.build_management()
        self.queue_restore()
        self.after(100,self.poll);self.protocol('WM_DELETE_WINDOW',self.close)
        self.task(lambda:load_or_extract(ROOT/'assets/Biaso4.png',self.config_path),self.geometry_done,'Đang nạp bản đồ điểm…')
    def button(self,parent,label,func):return ttk.Button(parent,text=label,command=lambda:self.guard(func))
    def guard(self,func):
        if self.busy:return
        try:func()
        except Exception as e:self.status.set(str(e));messagebox.showerror('Không hoàn tất',str(e))
    def show(self,widget,im):
        widget.source_image=im.copy();self.resize_preview(widget)
    def resize_preview(self,widget):
        im=getattr(widget,'source_image',None)
        if im is None:return
        p=Image.fromarray(cv2.cvtColor(im,cv2.COLOR_BGR2RGB));p.thumbnail((max(100,widget.winfo_width()-8),max(100,widget.winfo_height()-8)))
        photo=ImageTk.PhotoImage(p);widget.configure(image=photo,text='');widget.image=photo
    def task(self,func,done,text='Đang xử lý…'):
        self.busy=True;self.status.set(text)
        def run():
            try:self.messages.put((done,func(),None))
            except Exception as e:self.messages.put((done,None,str(e)))
        threading.Thread(target=run,daemon=True).start()
    def poll(self):
        try:
            done,value,error=self.messages.get_nowait();self.busy=False
            if error:self.status.set('Không hoàn tất: '+error);messagebox.showerror('Cần kiểm tra',error)
            else:self.guard(lambda:done(value))
        except queue.Empty:pass
        self.after(100,self.poll)
    def geometry_done(self,c):
        self.config=c;self.default_config=copy.deepcopy(c);self.summary.set('Chọn người bắn rồi chụp hoặc mở ảnh trước.')
        self.status.set('Sẵn sàng. Đã nạp bản đồ điểm Score.png.')
        if not self.recovery_offered:self.recovery_offered=True;self.after(150,lambda:self.guard(self.offer_recovery))
    def rebuild_template(self):
        if self.before_image is not None:raise ValueError('Hãy kết thúc hoặc bỏ loạt hiện tại trước khi đổi cấu hình')
        self.task(lambda:load_or_extract(self.template_path,self.config_path),self.geometry_done)
    def camera(self):
        if self.cap:self.cap.release();self.cap=None;self.frame=None;self.refresh_images();return
        number=int(self.vars['camera'].get());backends=[cv2.CAP_DSHOW,cv2.CAP_MSMF] if os.name=='nt' else [cv2.CAP_ANY]
        for backend in backends:
            cap=cv2.VideoCapture(number,backend)
            if cap.isOpened():self.cap=cap;break
            cap.release()
        if not self.cap:raise ValueError('Không mở được USB camera. Thử chỉ số khác tại Thiết lập; đóng ứng dụng đang dùng camera.')
        self.tick()
    def tick(self):
        if not self.cap:return
        ok,frame=self.cap.read();self.frame=frame if ok else None
        if ok and self.before_image is None:self.show(self.views[0],frame)
        self.after(100,self.tick)
    def read_meta(self):
        m={k:self.vars[k].get().strip() for k in ['shooter_code','shooter','unit','lane','series']}
        if any(not v for v in m.values()):raise ValueError('Điền mã, họ tên, đơn vị, bệ bắn và loạt trước khi chụp')
        m['lane']=int(m['lane']);m['series']=int(m['series']);e=self.vars['expected'].get().strip();m['expected_shots']=int(e) if e else None
        if m['lane']<1 or m['series']<1 or (m['expected_shots'] is not None and m['expected_shots']<0):raise ValueError('Bệ/loạt phải dương; số phát không âm')
        try:m['shooting_date']=date.fromisoformat(self.vars['shooting_date'].get().strip()).isoformat()
        except ValueError:raise ValueError('Ngày bắn phải là ngày hợp lệ dạng YYYY-MM-DD')
        if any(len(m[k])>200 for k in ('shooter_code','shooter','unit')):raise ValueError('Mã, họ tên, đơn vị tối đa 200 ký tự')
        m['notes']=self.vars['notes'].get().strip()
        return m
    def check_can_set(self,kind):
        if not self.config:raise ValueError('Bản đồ điểm chưa nạp xong')
        if self.saved:raise ValueError('Loạt đã lưu; bấm Loạt tiếp theo hoặc Người tiếp theo để chụp tiếp. Muốn sửa, dùng tab Thiết lập.')
        if kind=='before':
            if self.before_image is not None:raise ValueError('Đã có ảnh trước. Bấm Loạt tiếp theo nếu muốn bắt đầu lại.')
            return self.read_meta()
        if self.before_image is None:raise ValueError('Cần ảnh trước loạt')
    def set_image(self,kind,im,source,preview=True,extra=None):
        self.check_can_set(kind)
        if kind=='before':
            self.meta=self.read_meta();self.queue_check_before(source);self.meta['capture_sources']={'before':source};self.meta['before_acquired_at']=utc();self.round_id=str(uuid.uuid4())
            self.meta['_template_sha256']=sha(self.template_path);self.meta['_draft_config']=copy.deepcopy(self.config)
            self.before_image=im.copy()
            for e in self.identity_entries:e.configure(state='disabled')
        else:
            self.meta['after_acquired_at']=utc();self.meta['capture_sources']['after']=source;self.after_image=im.copy()
        self.meta.pop(kind+'_rectification',None)
        if extra:self.meta.update(extra)
        self.store.capture(self.round_id,kind,im,self.meta);self.show(self.views[0 if kind=='before' else 1],im);self.pairs=None
        if kind=='after':self.run_analysis()
        else:
            self.summary.set('Đã lưu ảnh trước. Kết thúc loạt thì chụp ảnh sau.');self.status.set('Ảnh trước đã được lưu ngay trên máy.')
            if preview:self.detect_before()
    def detect_before(self):
        image=self.before_image.copy();conf=float(self.vars['confidence'].get());rectified=self.rectification('before')
        def work():
            if self.detector is None:self.detector=Detector(ROOT/'assets/best.pt')
            H=prealigned_quality(image,self.template)[0] if rectified else align(image,self.template)[0];height,width=self.template.shape[:2]
            holes=self.detector.detect(cv2.warpPerspective(image,H,(width,height)),conf)
            points=transform([h['point'] for h in holes],np.linalg.inv(H))
            return [dict(point=p,label=f'C{i+1}',old=True,index=i,confidence=h.get('confidence',0)) for i,(h,p) in enumerate(zip(holes,points))]
        def done(items):
            self.before_preview=items;self.refresh_images();self.tree.delete(*self.tree.get_children())
            for item in items:self.tree.insert('','end',iid=str(item['index']),values=(item['label'],'Không','—',f"{item['confidence']:.0%}",'Lỗ cũ trên ảnh trước'))
            self.summary.set(f'Ảnh trước: {len(items)} lỗ cũ (đỏ). Sẵn sàng chụp ảnh sau.')
        self.task(work,done,'Đang nhận diện các lỗ cũ trên ảnh trước…')
    def capture(self,kind):
        if self.frame is None:raise ValueError('Mở camera trước; chưa có khung hình hợp lệ')
        self.set_image(kind,self.frame,'usb_camera')
    def load(self,kind):
        """Mở ảnh từ máy: nắn thẳng, cắt bỏ cảnh vật (rectify.py) rồi mới dùng; giữ bản sao ảnh gốc."""
        self.check_can_set(kind)
        p=filedialog.askopenfilename(filetypes=[('Ảnh','*.png *.jpg *.jpeg *.bmp')])
        if not p:return
        im=read_image(p);source='file:'+Path(p).name;name='trước' if kind=='before' else 'sau'
        def work():
            try:return rectify(im,self.template,self.config)
            except ValueError as e:return None,{'failed':str(e)}
        def done(res):
            out,info=res
            if out is None:
                if not messagebox.askyesno('Không nắn được ảnh',info['failed']+f'\n\nVẫn dùng ảnh gốc (chưa xử lý) làm ảnh {name}?'):
                    self.status.set('Đã hủy. Chụp lại ảnh rõ, thấy toàn bộ bia rồi mở lại.');return
                out=im
            self.set_image(kind,out,source,extra={kind+'_rectification':info})
            setattr(self,kind+'_original',im)
            try:shutil.copyfile(p,self.store.root/'drafts'/self.round_id/(kind+'_original'+Path(p).suffix.lower()))
            except OSError as e:self.status.set('Không lưu được bản sao ảnh gốc: '+str(e))
        self.task(work,done,f'Đang xử lý ảnh {name}: SIFT tìm bia, nắn thẳng và cắt bỏ cảnh vật xung quanh…')
    def rectification(self,kind):
        """Thông tin nắn ảnh nếu ảnh hiện tại đã được nắn về khung ảnh mẫu, ngược lại None."""
        info=(self.meta or {}).get(kind+'_rectification') or (self.result or {}).get(kind+'_rectification')
        im=getattr(self,kind+'_image')
        if info and info.get('homography') and im is not None and im.shape[:2]==self.template.shape[:2]:return info
        return None
    def original_file(self,kind):
        """Bản sao ảnh gốc trong thư mục nháp của loạt (để xem lại sau khi khôi phục / mở lịch sử)."""
        if not self.round_id:return None
        files=sorted((self.store.root/'drafts'/self.round_id).glob(kind+'_original.*'))
        return files[0] if files else None
    def has_original(self,kind):
        return getattr(self,kind+'_original') is not None or self.original_file(kind) is not None
    def original_menu(self):
        """Menu chọn xem ảnh gốc của ảnh trước hay ảnh sau; mục chưa có ảnh bị làm mờ."""
        menu=tk.Menu(self,tearoff=0)
        for kind,label in (('before','Ảnh gốc của ảnh TRƯỚC'),('after','Ảnh gốc của ảnh SAU')):
            ok=self.has_original(kind)
            menu.add_command(label=label if ok else label+' (chưa có)',state='normal' if ok else 'disabled',
                             command=lambda k=kind:self.guard(lambda:self.show_original(k)))
        return menu
    def choose_original(self):
        if not (self.has_original('before') or self.has_original('after')):
            raise ValueError('Chưa có ảnh gốc. Ảnh gốc chỉ có khi bấm "Mở ảnh trước" / "Mở ảnh sau & tính điểm" và chọn ảnh từ máy tính.')
        menu=self.original_menu();b=self.original_button
        try:menu.tk_popup(b.winfo_rootx(),b.winfo_rooty()+b.winfo_height())
        finally:menu.grab_release()
    def show_original(self,kind='before'):
        name='trước' if kind=='before' else 'sau'
        im=getattr(self,kind+'_original');info=(self.meta or {}).get(kind+'_rectification') or (self.result or {}).get(kind+'_rectification')
        if im is None and self.original_file(kind):im=read_image(self.original_file(kind))
        if im is None:raise ValueError(f'Chưa có ảnh gốc của ảnh {name}. Ảnh gốc chỉ có khi mở ảnh {name} từ máy tính.')
        window=self.zoom_windows.get('original_'+kind)
        if window and window.winfo_exists():window.destroy()
        self.zoom_windows['original_'+kind]=OriginalView(self,im,info,title='Ảnh gốc — ảnh '+name)
    def demo(self):
        if self.before_image is not None:raise ValueError('Bấm Loạt tiếp theo trước khi thử ảnh mẫu')
        for k,v in {'shooter_code':'DEMO','shooter':'Thử ảnh mẫu','unit':'DEMO'}.items():self.vars[k].set(v)
        self.set_image('before',self.template,'reference_template_demo',preview=False)
        self.meta['demo']=True
        self.set_image('after',read_image(ROOT/'assets/2.Ket_thuc_New68.bmp'),'sample_after_demo')
    def run_analysis(self):
        if self.before_image is None or self.after_image is None:raise ValueError('Cần đủ hai ảnh')
        if not self.config or not self.config.get('confirmed'):raise ValueError('Cấu hình bia của loạt chưa được xác nhận')
        if self.saved and not messagebox.askyesno('Tính lại loạt','Tính lại sẽ thay các hiệu chỉnh hiện tại bằng kết quả nhận diện mới và lưu phiên bản tiếp theo. Tiếp tục?'):return
        conf=float(self.vars['confidence'].get());radius=float(self.vars['radius'].get())
        if not 0<conf<1 or not 0<radius<=100:raise ValueError('Ngưỡng YOLO phải trong (0,1), khoảng ghép trong (0,100]')
        prealigned=(self.rectification('before'),self.rectification('after'))
        def work():
            if self.detector is None:self.detector=Detector(ROOT/'assets/best.pt')
            r=analyze(self.before_image,self.after_image,self.template,self.config,self.detector,conf,radius,self.pairs,prealigned)
            r['model_sha256']=sha(ROOT/'assets/best.pt');count=sum(x['selected'] for x in r['detections'])
            if self.meta.get('expected_shots') is not None and count!=self.meta['expected_shots']:r['warnings'].append('Số lỗ mới khác số phát dự kiến')
            if self.meta.get('demo'):r['warnings'].append('Ảnh trước là mẫu tham chiếu, không phải ảnh đầu loạt thực tế');r['status']='DEMO'
            elif r['warnings']:r['status']='NEEDS_REVIEW'
            return r
        self.task(work,self.analysis_done,'Đang căn chỉnh hai ảnh, nhận diện YOLO và tính điểm trên mẫu…')
    def analysis_done(self,result):
        self.meta.pop('reviewed_at',None);self.result=result;self.persist();self.refresh();self.queue_on_scored()
    def persist(self):
        rescore_result(self.result)
        self.meta['processed_at']=utc()
        folder,payload=self.store.save(self.result,self.meta,self.before_image,self.after_image,draw_markers(self.after_image,markers(self.result,'after')),self.round_id,expected_revision=self.expected_revision)
        self.result=payload;self.expected_revision=payload['revision']
        if not self.meta.get('demo'):
            row={'code':self.meta['shooter_code'],'name':self.meta['shooter'],'unit':self.meta['unit']}
            if not any(r['code']==row['code'] for r in self.store.list_shooters()):self.store.save_shooters([row]);self.refresh_roster()
        self.saved=True;self.status.set('Đã tự lưu ảnh và kết quả: '+str(folder))
        if self.auto_sync.get() and self.password.get():self.sync()
    def refresh(self):
        if not self.result:return
        self.tree.delete(*self.tree.get_children());holes=self.result['detections']
        numbers=hole_labels(holes)
        for i,h in enumerate(holes):
            label='Lỗ cũ' if is_old(h) else 'Mới'
            if not h['selected']:label+=' • Không cộng điểm'
            elif h['score']==0:label+=' • Ngoài vùng điểm (pixel 0)'
            if h.get('issues'):label+=' • '+'; '.join(h['issues'])
            self.tree.insert('','end',iid=str(i),values=(numbers[i],'Có' if h['selected'] else 'Bỏ',h['score'] if h['selected'] else 0,'Thủ công' if h.get('source')=='manual' else f"{h.get('confidence',0):.0%}",label))
        self.refresh_images()
        count=sum(h['selected'] for h in holes);total=sum(h['score'] for h in holes if h['selected'])
        label={'AUTO':'Tự động','NEEDS_REVIEW':'Cần kiểm tra','REVIEWED':'Đã kiểm tra','DEMO':'Thử nghiệm ảnh mẫu'}[self.result['status']]
        inside=sum(bool(h['selected'] and h['score']>0) for h in holes)
        self.summary.set(f'{label} • {count} lỗ được giữ, {inside} trong vùng điểm • Tổng {total} điểm • '+(' | '.join(self.result['warnings'][:2])+(' …' if len(self.result['warnings'])>2 else '') if self.result['warnings'] else 'Đã lưu.'))
        self.refresh_history_safely()
        if self.inspector and self.inspector.winfo_exists():self.inspector.update_data()
    def open_inspection(self):
        return self.open_image('after' if self.after_image is not None else 'before')
    def image_items(self,kind):
        if self.result:return markers(self.result,kind)
        return self.before_preview if kind=='before' else []
    def image_selected(self,kind):
        selected=self.tree.selection()
        if not selected:return None
        index=int(selected[0])
        if kind=='before' and self.result:return self.result['detections'][index].get('matched_before')
        return index
    def refresh_images(self):
        for i,kind in enumerate(('before','after')):
            im=getattr(self,kind+'_image')
            if im is not None:self.show(self.views[i],draw_markers(im,self.image_items(kind),self.image_selected(kind)))
            window=self.zoom_windows.get(kind)
            if window and window.winfo_exists():window.render()
    def selection_changed(self):
        self.refresh_images()
        if self.inspector and self.inspector.winfo_exists():self.inspector.render()
    def open_image(self,kind):
        if getattr(self,kind+'_image') is None:raise ValueError('Chưa có ảnh '+('trước' if kind=='before' else 'sau'))
        window=self.zoom_windows.get(kind)
        if window and window.winfo_exists():window.lift();return
        self.zoom_windows[kind]=ImagePreview(self,kind)
    def manual(self):
        if self.before_image is None or self.after_image is None:raise ValueError('Cần đủ hai ảnh')
        pairs=[]
        for label,im in [('TRƯỚC',self.before_image),('SAU',self.after_image)]:
            src=Picker(self,im,label+': chọn ít nhất 4 điểm trải đều quanh bia',4).result
            if src is None:return
            dst=Picker(self,self.template,f'Chọn {len(src)} điểm tương ứng trên mẫu theo CÙNG THỨ TỰ',len(src),len(src)).result
            if dst is None:return
            pairs.append((src,dst))
        self.pairs=pairs;self.run_analysis()
    def inspect(self):
        if not self.result:raise ValueError('Chưa có kết quả căn chỉnh')
        for kind in ['before','after']:
            H=np.array(self.result['homography_'+kind]);im=cv2.warpPerspective(getattr(self,kind+'_image'),H,(self.template.shape[1],self.template.shape[0]))
            Picker(self,cv2.addWeighted(im,.5,self.template,.5,0),'Ảnh '+kind+' chồng với mẫu: kiểm tra các đường nét có khớp không',0,0)
    def changed(self):
        self.result['status']='DEMO' if self.meta.get('demo') else 'NEEDS_REVIEW'
        self.meta.pop('reviewed_at',None);self.result.pop('reviewed_at',None);self.result['warnings'].append('Kết quả đã sửa, chưa xác nhận kiểm tra');self.persist();self.refresh()
    def toggle(self):
        if not self.result:return
        selected=self.tree.selection()
        if not selected:raise ValueError('Chọn một dòng ở tab Loạt bắn trước')
        for item in selected:
            h=self.result['detections'][int(item)];h['selected']=not h['selected'];h['review_edited']=True
        self.changed()
    def edit_hole(self,new=False):
        if not self.result:return
        sel=() if new else self.tree.selection()
        p=Picker(self,draw_markers(self.after_image,markers(self.result,'after')),'Ảnh SAU: nhấp tâm lỗ để '+('sửa lỗ đã chọn' if sel else 'thêm lỗ mới')+'; cuộn để phóng to',1,1).result
        if p is None:return
        self.set_manual_hole(p[0],int(sel[0]) if sel else None)
    def remove_hole(self,index):
        if not self.result:raise ValueError('Chưa có kết quả để sửa')
        hole=self.result['detections'][index]
        hole['selected']=False;hole['review_edited']=True;hole['excluded']=True
        self.changed()
    def classify_hole(self,index,classification):
        if classification not in ('old','new'):raise ValueError('Chọn lỗ cũ hoặc lỗ mới')
        hole=self.result['detections'][index]
        hole['classification']=classification;hole['selected']=classification=='new'
        hole['excluded']=False;hole['review_edited']=True
        self.changed()
    def set_manual_hole(self,original_point,index=None,radius=None):
        if not self.result:raise ValueError('Cần tính điểm hai ảnh trước khi sửa lỗ')
        x,y=map(float,original_point);height,width=self.after_image.shape[:2]
        if not np.isfinite([x,y]).all() or not (0<=x<width and 0<=y<height):raise ValueError('Tâm lỗ phải nằm trong ảnh sau')
        if radius is not None and (not np.isfinite(radius) or radius<=0):raise ValueError('Bán kính vòng tròn phải dương')
        point=transform([[x,y]],np.array(self.result['homography_after']))[0]
        old=self.result['detections'][index] if index is not None else None
        h={'template_point':point,'original_point':[x,y],'score':score_point(point,self.result['config']),'selected':True if old is None else old['selected'],'confidence':0.,'source':'manual','matched_before':None if old is None else old.get('matched_before'),'issues':[],'review_edited':True}
        h['excluded']=False if old is None else old.get('excluded',False)
        if old is not None and 'classification' in old:h['classification']=old['classification']
        if radius is None and old is not None:radius=old.get('manual_radius_original')
        if radius is not None:h['manual_radius_original']=float(radius)
        if old is not None:
            h['previous']=copy.deepcopy(old);self.result['detections'][index]=h
        else:self.result['detections'].append(h)
        self.changed()
    def review(self):
        if not self.result:return
        if not messagebox.askyesno('Xác nhận kiểm tra','Bạn đã kiểm tra căn chỉnh, lỗ cũ/mới và điểm trên ảnh?'):return
        self.result['review_warnings']=self.result['warnings'];self.result['warnings']=[]
        self.result['status']='DEMO' if self.meta.get('demo') else 'REVIEWED';self.meta['reviewed_at']=utc();self.persist();self.refresh()
    def sync(self):
        if not self.password.get():raise ValueError('Nhập mật khẩu quản lý trong Thiết lập để gửi lên server')
        url=self.url.get().strip();pw=self.password.get()
        def done(n):self.refresh_history_safely();self.status.set(f'Đã gửi {n} loạt. Kết quả nằm ở {url}/real')
        self.task(lambda:self.store.sync(url,pw),done,'Đang gửi kết quả; nếu mất mạng dữ liệu vẫn được giữ…')
    def clear_round(self):
        if self.inspector and self.inspector.winfo_exists():self.inspector.destroy()
        self.inspector=None;self.expected_revision=None;self.before_preview=[]
        for window in self.zoom_windows.values():
            if window.winfo_exists():window.destroy()
        self.zoom_windows={}
        self.before_image=None;self.before_original=None;self.after_image=None;self.after_original=None;self.result=None;self.pairs=None;self.round_id=None;self.saved=False;self.meta=None
        self.config=copy.deepcopy(self.default_config)
        for e in self.identity_entries:e.configure(state='normal')
        self.tree.delete(*self.tree.get_children())
        for v in self.views:v.source_image=None;v.configure(image='',text='Chưa có ảnh')
    def reset(self,new_shooter=False):
        if not self.may_replace_round():return False
        try:series=int(self.vars['series'].get())+1
        except ValueError:series=1
        self.clear_round();self.vars['series'].set('1' if new_shooter else str(series));self.vars['shooting_date'].set(datetime.now(LOCAL_TZ).date().isoformat());self.vars['notes'].set('')
        if new_shooter:
            for key in ('shooter_code','shooter','unit','expected'):self.vars[key].set('')
        self.summary.set('Chọn người bắn và chụp ảnh trước.');self.status.set('Loạt mới. Bấm Tìm / chọn người bắn để chọn từ danh sách.');return True
    def close(self):
        if self.busy:messagebox.showinfo('Đang xử lý','Đợi xử lý hoặc gửi dữ liệu xong rồi đóng.');return
        if self.cap:self.cap.release()
        self.destroy()

if __name__=='__main__':App().mainloop()
