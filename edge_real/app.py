"""Automatic pair capture workflow. Advanced editing stays off the main path."""
import copy
import json
import os
import queue
import threading
import uuid
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
import cv2
import numpy as np
from PIL import Image,ImageTk
from core import read_image,sha,validate_config,overlay,analyze,Detector,score_point
from storage import Store,utc,atomic_json
from template_geometry import load_or_extract
from picker import Picker
ROOT=Path(__file__).resolve().parents[1]

class App(tk.Tk):
    def __init__(self):
        super().__init__();self.title('Chấm điểm bia — Tự động v2 / CPU');self.geometry(f'1150x{min(850,self.winfo_screenheight()-90)}');self.minsize(900,620)
        self.template=read_image(ROOT/'assets/Biaso4.png');self.config_path=ROOT/'target_config.json';self.config=None
        self.before_image=None;self.after_image=None;self.result=None;self.busy=False;self.cap=None;self.frame=None
        self.detector=None;self.pairs=None;self.round_id=None;self.meta=None;self.saved=False;self.messages=queue.Queue();self.store=Store(ROOT/'data')
        self.vars={};self.identity_entries=[];self.photos=[]
        style=ttk.Style();style.configure('Title.TLabel',font=('Segoe UI',17,'bold'));style.configure('TButton',padding=6)
        ttk.Label(self,text='Chấm điểm tự động từ hai ảnh',style='Title.TLabel').pack(anchor='w',padx=16,pady=(12,3))
        self.notebook=ttk.Notebook(self);self.notebook.pack(fill='both',expand=True,padx=12,pady=8)
        main_tab=ttk.Frame(self.notebook);advanced=ttk.Frame(self.notebook);self.notebook.add(main_tab,text='Loạt bắn');self.notebook.add(advanced,text='Thiết lập & kiểm tra')
        scroll=tk.Canvas(main_tab,highlightthickness=0);sb=ttk.Scrollbar(main_tab,orient='vertical',command=scroll.yview);scroll.configure(yscrollcommand=sb.set)
        sb.pack(side='right',fill='y');scroll.pack(side='left',fill='both',expand=True);main=ttk.Frame(scroll)
        content=scroll.create_window((0,0),window=main,anchor='nw')
        main.bind('<Configure>',lambda e:scroll.configure(scrollregion=scroll.bbox('all')))
        scroll.bind('<Configure>',lambda e:scroll.itemconfigure(content,width=e.width))
        self.bind('<MouseWheel>',lambda e:scroll.yview_scroll(-int(e.delta/120),'units') if self.notebook.index('current')==0 else None)
        form=ttk.Frame(main);form.pack(fill='x',pady=7)
        for i,(key,label,default) in enumerate([('shooter_code','Mã người bắn','S01'),('shooter','Họ tên',''),('unit','Đơn vị',''),('lane','Làn','1'),('series','Loạt','1'),('expected','Số phát (nếu biết)','')]):
            ttk.Label(form,text=label).grid(row=0,column=i,sticky='w',padx=5);v=tk.StringVar(value=default);self.vars[key]=v
            e=ttk.Entry(form,textvariable=v,width=17 if i<3 else 11);e.grid(row=1,column=i,padx=5,pady=4);self.identity_entries.append(e)
        bar=ttk.Frame(main);bar.pack(fill='x')
        for name,cmd in [('Mở / đóng camera',self.camera),('1. Chụp TRƯỚC',lambda:self.capture('before')),('2. Chụp SAU & tính điểm',lambda:self.capture('after')),('Loạt mới',self.reset)]:self.button(bar,name,cmd).pack(side='left',padx=4)
        files=ttk.Frame(main);files.pack(fill='x',pady=5)
        for name,cmd in [('Mở ảnh trước',lambda:self.load('before')),('Mở ảnh sau & tính điểm',lambda:self.load('after')),('Thử hai ảnh đã gửi',self.demo)]:self.button(files,name,cmd).pack(side='left',padx=4)
        ttk.Label(main,text='Ảnh được lưu ngay. Khi nhận ảnh sau, hệ thống tự căn chỉnh → YOLO → đối chiếu → tính điểm → lưu.').pack(anchor='w',padx=5,pady=5)
        panels=ttk.Frame(main);panels.pack(fill='x');self.views=[]
        for label in ['Camera / ảnh trước','Ảnh sau','Điểm trên bia mẫu']:
            p=ttk.LabelFrame(panels,text=label);p.pack(side='left',fill='both',expand=True,padx=3);v=ttk.Label(p);v.pack();self.views.append(v)
        self.summary=tk.StringVar(value='Đang chuẩn bị bia mẫu…');ttk.Label(main,textvariable=self.summary,font=('Segoe UI',12,'bold'),wraplength=1070).pack(anchor='w',padx=5,pady=7)
        self.tree=ttk.Treeview(main,columns=('take','xy','score','conf','status'),show='headings',height=5)
        for key,label,width in [('take','Tính loạt này',85),('xy','Tâm trên mẫu (px)',150),('score','Điểm',60),('conf','Tin cậy',70),('status','Đối chiếu / lưu ý',460)]:self.tree.heading(key,text=label);self.tree.column(key,width=width)
        self.tree.pack(fill='both',expand=True,padx=5);self.tree.bind('<Escape>',lambda e:self.tree.selection_remove(*self.tree.selection()))
        self.button(main,'Mở kết quả trên Server',lambda:webbrowser.open(self.url.get().rstrip('/')+'/real')).pack(anchor='e',pady=5)
        self.url=tk.StringVar(value='http://127.0.0.1:8000');self.password=tk.StringVar();self.auto_sync=tk.BooleanVar(value=True)
        self.vars['camera']=tk.StringVar(value='0');self.vars['confidence']=tk.StringVar(value='.25');self.vars['radius']=tk.StringVar(value='12')
        settings=ttk.LabelFrame(advanced,text='Thiết lập một lần');settings.pack(fill='x',padx=8,pady=8)
        for row,(label,var) in enumerate([('Chỉ số camera USB',self.vars['camera']),('Ngưỡng YOLO',self.vars['confidence']),('Khoảng ghép lỗ (pixel mẫu)',self.vars['radius']),('Server',self.url),('Mật khẩu quản lý',self.password)]):
            ttk.Label(settings,text=label).grid(row=row,column=0,sticky='w',padx=8,pady=5);ttk.Entry(settings,textvariable=var,width=40,show='*' if row==4 else '').grid(row=row,column=1,sticky='w',padx=8)
        ttk.Checkbutton(settings,text='Tự gửi kết quả sau khi lưu (cần mật khẩu; mất mạng sẽ giữ hàng đợi)',variable=self.auto_sync).grid(row=5,columnspan=2,sticky='w',padx=8,pady=5)
        self.button(settings,'Gửi lại các loạt đang chờ',self.sync).grid(row=6,column=1,sticky='w',pady=6)
        tools=ttk.LabelFrame(advanced,text='Chỉ dùng khi cần kiểm tra hoặc sửa');tools.pack(fill='x',padx=8,pady=8)
        for row,(name,cmd) in enumerate([('Xem / xác nhận bia mẫu',self.confirm_template),('Tự nhận dạng lại bia mẫu',self.rebuild_template),('Căn chỉnh thủ công khi tự động thất bại',self.manual),('Xem hai ảnh chồng với mẫu',self.inspect),('Đổi tính / bỏ lỗ đang chọn',self.toggle),('Thêm / sửa tâm lỗ trên mẫu',self.edit_hole),('Xác nhận đã kiểm tra kết quả',self.review)]):self.button(tools,name,cmd).grid(row=row,column=0,sticky='w',padx=8,pady=3)
        ttk.Label(tools,text='Chọn dòng ở tab Loạt bắn rồi sửa tại đây.\nNhấn Escape trong bảng để bỏ chọn trước khi thêm lỗ.\nLỗ trúng chồng hoàn toàn không thể đếm chắc chắn từ hai ảnh.',wraplength=460).grid(row=0,column=1,rowspan=5,padx=15)
        self.status=tk.StringVar(value='Đang tìm đường bao và các vòng điểm…');ttk.Label(self,textvariable=self.status,wraplength=1100).pack(fill='x',padx=16,pady=(0,10))
        self.after(100,self.poll);self.protocol('WM_DELETE_WINDOW',self.close)
        self.task(lambda:load_or_extract(ROOT/'assets/Biaso4.png',self.config_path),self.geometry_done,'Đang nhận dạng bia mẫu…')
    def button(self,parent,label,func):return ttk.Button(parent,text=label,command=lambda:self.guard(func))
    def guard(self,func):
        if self.busy:return
        try:func()
        except Exception as e:self.status.set(str(e));messagebox.showerror('Không hoàn tất',str(e))
    def show(self,widget,im):
        p=Image.fromarray(cv2.cvtColor(im,cv2.COLOR_BGR2RGB));p.thumbnail((330,225));photo=ImageTk.PhotoImage(p);widget.configure(image=photo);widget.image=photo
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
        self.config=c;self.show(self.views[2],overlay(self.template,c));self.summary.set('Bia mẫu đã có đường bao và 5 vòng điểm tự động.')
        if not c.get('confirmed'):self.confirm_template()
        else:self.status.set('Sẵn sàng. Điền người bắn rồi chụp ảnh trước.')
    def confirm_template(self):
        if self.config is None:raise ValueError('Chưa tạo được cấu hình bia mẫu')
        # Only one visual check, no point-by-point calibration.
        Picker(self,overlay(self.template,self.config),'Kiểm tra một lần: đường bao xanh và các vòng vàng có khớp bia? Đóng cửa sổ để trả lời.',0,0)
        if messagebox.askyesno('Xác nhận mẫu một lần','Đường bao và các vòng tự tìm đã khớp ảnh mẫu?\nSau khi xác nhận, các loạt sau dùng lại cấu hình này.'):
            self.config['confirmed']=True;self.config['confirmed_at']=utc();atomic_json(self.config_path,self.config);self.status.set('Đã xác nhận mẫu. Không cần hiệu chỉnh lại cho mỗi loạt.')
        else:self.status.set('Mẫu chưa xác nhận. Kiểm tra ảnh mẫu hoặc chọn Tự nhận dạng lại. Chưa chấm điểm.')
    def rebuild_template(self):
        if self.before_image is not None:raise ValueError('Hãy kết thúc hoặc bỏ loạt hiện tại trước khi đổi cấu hình')
        from template_geometry import extract_geometry
        def work():
            c=extract_geometry(self.template);c['template_sha256']=sha(ROOT/'assets/Biaso4.png');atomic_json(self.config_path,c);return c
        self.task(work,self.geometry_done)
    def camera(self):
        if self.cap:self.cap.release();self.cap=None;self.frame=None;return
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
        if ok:self.show(self.views[0],frame)
        self.after(100,self.tick)
    def read_meta(self):
        m={k:self.vars[k].get().strip() for k in ['shooter_code','shooter','unit','lane','series']}
        if any(not v for v in m.values()):raise ValueError('Điền mã, họ tên, đơn vị, làn và loạt trước khi chụp')
        m['lane']=int(m['lane']);m['series']=int(m['series']);e=self.vars['expected'].get().strip();m['expected_shots']=int(e) if e else None
        if m['lane']<1 or m['series']<1 or (m['expected_shots'] is not None and m['expected_shots']<0):raise ValueError('Làn/loạt phải dương; số phát không âm')
        return m
    def set_image(self,kind,im,source):
        if not self.config or not self.config.get('confirmed'):raise ValueError('Xem và xác nhận bia mẫu một lần ở tab Thiết lập')
        if self.saved:raise ValueError('Loạt đã lưu; bấm Loạt mới để chụp tiếp. Muốn sửa kết quả, dùng tab Thiết lập.')
        if kind=='before':
            if self.before_image is not None:raise ValueError('Đã có ảnh trước. Bấm Loạt mới nếu muốn thay.')
            self.meta=self.read_meta();self.meta['capture_sources']={'before':source};self.meta['before_acquired_at']=utc();self.round_id=str(uuid.uuid4())
            self.before_image=im.copy()
            for e in self.identity_entries:e.configure(state='disabled')
        else:
            if self.before_image is None:raise ValueError('Cần ảnh trước loạt')
            self.meta['after_acquired_at']=utc();self.meta['capture_sources']['after']=source;self.after_image=im.copy()
        self.store.capture(self.round_id,kind,im,self.meta);self.show(self.views[0 if kind=='before' else 1],im);self.pairs=None
        if kind=='after':self.run_analysis()
        else:self.summary.set('Đã lưu ảnh trước. Kết thúc loạt thì chụp ảnh sau.');self.status.set('Ảnh trước đã được lưu ngay trên máy.')
    def capture(self,kind):
        if self.frame is None:raise ValueError('Mở camera trước; chưa có khung hình hợp lệ')
        self.set_image(kind,self.frame,'usb_camera')
    def load(self,kind):
        p=filedialog.askopenfilename(filetypes=[('Ảnh','*.png *.jpg *.jpeg *.bmp')])
        if p:self.set_image(kind,read_image(p),'file:'+Path(p).name)
    def demo(self):
        if self.before_image is not None:raise ValueError('Bấm Loạt mới trước khi thử ảnh mẫu')
        for k,v in {'shooter_code':'DEMO','shooter':'Thử ảnh mẫu','unit':'DEMO'}.items():self.vars[k].set(v)
        self.set_image('before',self.template,'reference_template_demo')
        self.meta['demo']=True
        self.set_image('after',read_image(ROOT/'assets/2.Ket_thuc_New68.bmp'),'sample_after_demo')
    def run_analysis(self):
        if self.before_image is None or self.after_image is None:raise ValueError('Cần đủ hai ảnh')
        conf=float(self.vars['confidence'].get());radius=float(self.vars['radius'].get())
        if not 0<conf<1 or not 0<radius<=100:raise ValueError('Ngưỡng YOLO phải trong (0,1), khoảng ghép trong (0,100]')
        def work():
            if self.detector is None:self.detector=Detector(ROOT/'assets/best.pt')
            r=analyze(self.before_image,self.after_image,self.template,self.config,self.detector,conf,radius,self.pairs)
            r['model_sha256']=sha(ROOT/'assets/best.pt');count=sum(x['selected'] for x in r['detections'])
            if self.meta['expected_shots'] is not None and count!=self.meta['expected_shots']:r['warnings'].append('Số lỗ mới khác số phát dự kiến')
            if self.meta.get('demo'):r['warnings'].append('Ảnh trước là mẫu tham chiếu, không phải ảnh đầu loạt thực tế');r['status']='DEMO'
            elif r['warnings']:r['status']='NEEDS_REVIEW'
            return r
        self.task(work,self.analysis_done,'Đang căn chỉnh hai ảnh, nhận diện YOLO và tính điểm trên mẫu…')
    def analysis_done(self,result):self.result=result;self.persist();self.refresh()
    def persist(self):
        self.meta['processed_at']=utc()
        folder,payload=self.store.save(self.result,self.meta,self.before_image,self.after_image,overlay(self.template,self.config,self.result['detections']),self.round_id)
        self.saved=True;self.status.set('Đã tự lưu ảnh và kết quả: '+str(folder))
        if self.auto_sync.get() and self.password.get():self.sync()
    def refresh(self):
        if not self.result:return
        self.tree.delete(*self.tree.get_children());holes=self.result['detections']
        for i,h in enumerate(holes):
            label='Lỗ cũ' if h.get('matched_before') is not None else 'Mới'
            if h.get('issues'):label+=' • '+'; '.join(h['issues'])
            self.tree.insert('','end',iid=str(i),values=('Có' if h['selected'] else 'Bỏ',[round(v,1) for v in h['template_point']],h['score'],f"{h['confidence']:.2f}",label))
        self.show(self.views[2],overlay(self.template,self.config,holes))
        count=sum(h['selected'] for h in holes);total=sum(h['score'] for h in holes if h['selected'])
        label={'AUTO':'Tự động','NEEDS_REVIEW':'Cần kiểm tra','REVIEWED':'Đã kiểm tra','DEMO':'Thử nghiệm ảnh mẫu'}[self.result['status']]
        self.summary.set(f'{label} • {count} lỗ mới được tính • {total} điểm • '+(' | '.join(self.result['warnings'][:2])+(' …' if len(self.result['warnings'])>2 else '') if self.result['warnings'] else 'Đã lưu.'))
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
        self.meta.pop('reviewed_at',None);self.result['warnings'].append('Kết quả đã sửa, chưa xác nhận kiểm tra');self.persist();self.refresh()
    def toggle(self):
        if not self.result:return
        selected=self.tree.selection()
        if not selected:raise ValueError('Chọn một dòng ở tab Loạt bắn trước')
        for item in selected:
            h=self.result['detections'][int(item)];h['selected']=not h['selected'];h['review_edited']=True
        self.changed()
    def edit_hole(self):
        if not self.result:return
        sel=self.tree.selection();p=Picker(self,overlay(self.template,self.config,self.result['detections']),'Chọn tâm lỗ; đang chọn dòng: sửa, không chọn dòng: thêm',1,1).result
        if p is None:return
        h={'template_point':p[0],'score':score_point(p[0],self.config),'selected':True,'confidence':0.,'source':'manual','matched_before':None,'issues':[]}
        if sel:
            old=self.result['detections'][int(sel[0])];h['previous']=copy.deepcopy(old);self.result['detections'][int(sel[0])]=h
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
        self.task(lambda:self.store.sync(url,pw),lambda n:self.status.set(f'Đã gửi {n} loạt. Kết quả nằm ở {url}/real'),'Đang gửi kết quả; nếu mất mạng dữ liệu vẫn được giữ…')
    def reset(self):
        if self.before_image is not None and not self.saved and not messagebox.askyesno('Loạt mới','Loạt chưa xử lý xong. Ảnh đã lưu nháp; bắt đầu loạt mới?'):return
        self.before_image=None;self.after_image=None;self.result=None;self.pairs=None;self.round_id=None;self.saved=False;self.meta=None
        for e in self.identity_entries:e.configure(state='normal')
        self.vars['series'].set(str(int(self.vars['series'].get())+1));self.tree.delete(*self.tree.get_children());self.summary.set('Loạt mới: chọn người bắn và chụp ảnh trước.')
        for v in self.views[:2]:v.configure(image='')
    def close(self):
        if self.busy:messagebox.showinfo('Đang xử lý','Đợi xử lý hoặc gửi dữ liệu xong rồi đóng.');return
        if self.cap:self.cap.release()
        self.destroy()

if __name__=='__main__':App().mainloop()
