"""Local history, roster and recoverable drafts for the desktop application."""
import copy
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import ttk,filedialog,messagebox
from core import sha
from storage import shooting_day
from reports import STATES,export_xlsx,print_report,import_roster,roster_template


def table(parent,columns,height=12):
    frame=ttk.Frame(parent);frame.pack(fill='both',expand=True,padx=8,pady=6)
    tree=ttk.Treeview(frame,columns=[c[0] for c in columns],show='headings',height=height)
    for key,label,width in columns:tree.heading(key,text=label);tree.column(key,width=width,minwidth=65)
    y=ttk.Scrollbar(frame,command=tree.yview);x=ttk.Scrollbar(frame,orient='horizontal',command=tree.xview)
    tree.configure(yscrollcommand=y.set,xscrollcommand=x.set)
    tree.grid(row=0,column=0,sticky='nsew');y.grid(row=0,column=1,sticky='ns');x.grid(row=1,column=0,sticky='ew')
    frame.rowconfigure(0,weight=1);frame.columnconfigure(0,weight=1);return tree


class Management:
    def build_management(self):
        self.history_tab=ttk.Frame(self.notebook);self.roster_tab=ttk.Frame(self.notebook)
        self.notebook.add(self.history_tab,text='Lịch sử & báo cáo');self.notebook.add(self.roster_tab,text='Danh sách người bắn')
        filters=ttk.Frame(self.history_tab);filters.pack(fill='x',padx=8,pady=8);self.filters={}
        for i,(key,label,width) in enumerate([('query','Mã / tên / đơn vị',25),('day_from','Từ ngày YYYY-MM-DD',15),('day_to','Đến ngày YYYY-MM-DD',15),('lane','Bệ bắn',8)]):
            ttk.Label(filters,text=label).grid(row=0,column=i,padx=5,sticky='w');v=tk.StringVar();self.filters[key]=v
            entry=ttk.Entry(filters,textvariable=v,width=width);entry.grid(row=1,column=i,padx=5);entry.bind('<Return>',lambda e:self.guard(self.refresh_history))
        self.filters['status']=tk.StringVar();ttk.Label(filters,text='Trạng thái').grid(row=0,column=4)
        ttk.Combobox(filters,textvariable=self.filters['status'],state='readonly',width=17,values=['','AUTO','NEEDS_REVIEW','REVIEWED','DEMO']).grid(row=1,column=4)
        self.button(filters,'Tìm / làm mới',self.refresh_history).grid(row=1,column=5,padx=5)
        bar=ttk.Frame(self.history_tab);bar.pack(fill='x',padx=8)
        for label,cmd in [('Mở loạt đã chọn',self.open_history),('Xuất Excel theo bộ lọc',self.export_history),('In phiếu đã chọn',self.print_history),('Khôi phục loạt dang dở',self.drafts_window)]:self.button(bar,label,cmd).pack(side='left',padx=3)
        self.history_table=table(self.history_tab,[('code','Mã người bắn',110),('name','Họ tên',170),('unit','Đơn vị',150),('day','Ngày bắn',105),('series','Loạt',65),('lane','Bệ',65),('holes','Số lỗ',70),('score','Điểm',70),('status','Trạng thái',160),('sync','Đồng bộ',100)])
        self.history_table.bind('<Double-1>',lambda e:self.guard(self.open_history))
        self.history_summary=tk.StringVar();ttk.Label(self.history_tab,textvariable=self.history_summary).pack(anchor='w',padx=10,pady=6)
        ttk.Label(self.history_tab,text='Chọn nhiều dòng bằng Ctrl/Shift để in nhiều phiếu. Xuất Excel gồm toàn bộ kết quả khớp bộ lọc; DEMO được tổng hợp riêng.').pack(anchor='w',padx=10,pady=5)
        search=ttk.Frame(self.roster_tab);search.pack(fill='x',padx=8,pady=8);self.roster_query=tk.StringVar()
        ttk.Label(search,text='Tìm mã / tên / đơn vị').pack(side='left');entry=ttk.Entry(search,textvariable=self.roster_query,width=35);entry.pack(side='left',padx=8);entry.bind('<Return>',lambda e:self.guard(self.refresh_roster))
        self.button(search,'Tìm / làm mới',self.refresh_roster).pack(side='left')
        self.roster_table=table(self.roster_tab,[('code','Mã người bắn',150),('name','Họ tên',280),('unit','Đơn vị',260)],8)
        self.roster_table.bind('<<TreeviewSelect>>',self.roster_selected);self.roster_table.bind('<Double-1>',lambda e:self.guard(self.choose_shooter))
        form=ttk.Frame(self.roster_tab);form.pack(fill='x',padx=8,pady=6);self.roster_fields={}
        for i,(key,label) in enumerate([('code','Mã người bắn'),('name','Họ tên'),('unit','Đơn vị')]):
            ttk.Label(form,text=label).grid(row=0,column=i,sticky='w',padx=5);v=tk.StringVar();self.roster_fields[key]=v;ttk.Entry(form,textvariable=v,width=28).grid(row=1,column=i,padx=5)
        bar=ttk.Frame(self.roster_tab);bar.pack(fill='x',padx=8,pady=6)
        for label,cmd in [('Thêm người mới',lambda:self.save_roster(False)),('Sửa người đang chọn',lambda:self.save_roster(True)),('Chọn để bắn',self.choose_shooter),('Nhập danh sách CSV',self.import_shooters),('Tải CSV mẫu',self.save_roster_template)]:self.button(bar,label,cmd).pack(side='left',padx=3)
        ttk.Label(self.roster_tab,text='Mã người bắn là duy nhất. Sửa danh sách không thay đổi thông tin trên các loạt đã lưu. CSV dùng UTF-8, mở và chỉnh được bằng Excel.',wraplength=1050).pack(anchor='w',padx=10,pady=8)
        self.refresh_history();self.refresh_roster()

    def refresh_history(self):
        self.history_rows=self.store.list_rounds(**{k:v.get().strip() for k,v in self.filters.items()})
        selected=self.history_table.selection();self.history_table.delete(*self.history_table.get_children())
        for r in self.history_rows:
            self.history_table.insert('','end',iid=r['id'],values=[r.get('shooter_code'),r.get('shooter'),r.get('unit'),r.get('shooting_date'),r.get('series'),r.get('lane'),r.get('total_holes'),r.get('total_score'),STATES.get(r.get('status'),'Bản cũ'),'Đã gửi' if r['synced'] else 'Chờ gửi'])
        for item in selected:
            if self.history_table.exists(item):self.history_table.selection_add(item)
        real=[r for r in self.history_rows if r.get('status')!='DEMO'];demo=len(self.history_rows)-len(real)
        self.history_summary.set(f'{len(self.history_rows)} loạt • {len(real)} loạt thực tế, {sum(r.get("total_score") or 0 for r in real)} điểm (gồm chưa duyệt) • {demo} loạt DEMO')

    def refresh_history_safely(self):
        # Editing must remain successful even if an unfinished filter is invalid.
        try:self.refresh_history()
        except ValueError:self.history_summary.set('Bộ lọc chưa hợp lệ; sửa ngày / bệ rồi bấm Tìm / làm mới.')

    def may_replace_round(self):
        if self.before_image is not None and not self.saved:
            return messagebox.askyesno('Chuyển loạt','Loạt hiện tại chưa có kết quả. Ảnh đã lưu nháp và có thể khôi phục sau. Tiếp tục chuyển loạt?')
        return True

    def check_template(self,p,config):
        expected=p.get('_template_sha256') or config.get('template_sha256')
        if expected and expected!=sha(self.template_path):raise ValueError('Ảnh mẫu hiện tại khác ảnh mẫu của loạt này. Khôi phục đúng assets/Biaso4.png trước khi mở để sửa.')
        from core import validate_config
        validate_config(config)

    def fill_identity(self,p):
        for k in ('shooter_code','shooter','unit','lane','series'):self.vars[k].set(str(p.get(k) or (1 if k in ('lane','series') else '')))
        self.vars['shooting_date'].set(shooting_day(p));self.vars['expected'].set('' if p.get('expected_shots') is None else str(p['expected_shots']))
        self.vars['notes'].set(p.get('notes',''))
        for entry in self.identity_entries:entry.configure(state='disabled')

    def open_history(self):
        selected=self.history_table.selection()
        if len(selected)!=1:raise ValueError('Chọn đúng một loạt để mở')
        p,images=self.store.load_round(selected[0]);self.check_template(p,p['config'])
        if not self.may_replace_round():return
        self.clear_round();self.fill_identity(p);self.config=copy.deepcopy(p['config']);self.round_id=p['id'];self.result=p;self.saved=True;self.expected_revision=p.get('revision',1)
        self.result.setdefault('warnings',[]);self.result.setdefault('status','REVIEWED' if p.get('reviewed_at') else 'NEEDS_REVIEW')
        keys=['shooter_code','shooter','unit','lane','series','shooting_date','expected_shots','notes','before_acquired_at','after_acquired_at','capture_sources','demo','reviewed_at','_template_sha256']
        self.meta={k:copy.deepcopy(p[k]) for k in keys if k in p};self.meta.setdefault('shooting_date',shooting_day(p))
        self.before_image=images['before'];self.after_image=images['after'];self.refresh()
        for i,n in enumerate(('before','after')):self.show(self.views[i],images[n])
        if self.cap:self.cap.release();self.cap=None;self.frame=None
        self.notebook.select(0);self.status.set(f"Đã mở loạt {p.get('series')} / bệ {p.get('lane')} • phiên bản {p.get('revision',1)}. Sửa sẽ lưu thành phiên bản tiếp theo.")

    def export_history(self):
        self.refresh_history()
        if not self.history_rows:raise ValueError('Không có loạt khớp bộ lọc')
        path=filedialog.asksaveasfilename(defaultextension='.xlsx',initialfile='Ket_qua_ban.xlsx',filetypes=[('Excel','*.xlsx')])
        if path:
            export_xlsx(path,[self.store.load_payload(r['id']) for r in self.history_rows]);self.status.set('Đã xuất Excel: '+path)

    def print_history(self):
        selected=self.history_table.selection()
        if not selected:raise ValueError('Chọn ít nhất một loạt để in phiếu')
        path=filedialog.asksaveasfilename(defaultextension='.html',initialfile='Phieu_ket_qua.html',filetypes=[('Phiếu để in / lưu PDF','*.html')])
        if path:
            print_report(path,[self.store.load_payload(i) for i in selected],self.store);webbrowser.open(Path(path).resolve().as_uri());self.status.set('Đã tạo phiếu. Bấm In phiếu / Lưu PDF trong trình duyệt.')

    def refresh_roster(self):
        selected=self.roster_table.selection();self.roster_table.delete(*self.roster_table.get_children())
        for r in self.store.list_shooters(self.roster_query.get()):self.roster_table.insert('','end',iid=r['code'],values=(r['code'],r['name'],r['unit']))
        if selected and self.roster_table.exists(selected[0]):self.roster_table.selection_set(selected[0])
    def roster_selected(self,e=None):
        selected=self.roster_table.selection()
        if selected:
            values=self.roster_table.item(selected[0],'values')
            for key,value in zip(('code','name','unit'),values):self.roster_fields[key].set(value)
    def save_roster(self,replace):
        row={k:v.get().strip() for k,v in self.roster_fields.items()}
        if replace:
            selected=self.roster_table.selection()
            if len(selected)!=1 or selected[0]!=row['code']:raise ValueError('Chọn một người để sửa và giữ nguyên mã người bắn')
        elif any(r['code']==row['code'] for r in self.store.list_shooters()):raise ValueError('Mã đã tồn tại; dùng Sửa người đang chọn')
        self.store.save_shooters([row],replace=replace);self.refresh_roster();self.status.set('Đã lưu danh sách người bắn.')
    def choose_shooter(self):
        selected=self.roster_table.selection()
        if len(selected)!=1:raise ValueError('Chọn một người trong danh sách')
        values=self.roster_table.item(selected[0],'values')
        if not self.reset(new_shooter=True):return
        for key,value in zip(('shooter_code','shooter','unit'),values):self.vars[key].set(value)
        self.notebook.select(0)
    def import_shooters(self):
        path=filedialog.askopenfilename(filetypes=[('CSV UTF-8','*.csv')])
        if path:
            count=self.store.save_shooters(import_roster(path));self.refresh_roster();self.status.set(f'Đã nhập {count} người bắn. Không thay đổi thông tin của mã đã có khác nội dung.')
    def save_roster_template(self):
        path=filedialog.asksaveasfilename(defaultextension='.csv',initialfile='Danh_sach_nguoi_ban.csv',filetypes=[('CSV UTF-8','*.csv')])
        if path:roster_template(path);self.status.set('Đã tạo mẫu CSV: '+path)

    def offer_recovery(self):
        rows=self.store.list_drafts()
        if rows and messagebox.askyesno('Có loạt dang dở',f'Tìm thấy {len(rows)} loạt đã lưu ảnh nhưng chưa có kết quả. Mở danh sách khôi phục?'):self.drafts_window()
    def drafts_window(self):
        window=tk.Toplevel(self);window.title('Khôi phục loạt dang dở');window.geometry('1000x430')
        ttk.Label(window,text='Ảnh trước/sau đã được lưu ngay khi chụp. Chọn loạt để tiếp tục; nếu đã có hai ảnh, bấm Tính lại / thử lại.').pack(anchor='w',padx=10,pady=8)
        tree=table(window,[('name','Người bắn',220),('unit','Đơn vị',180),('day','Ngày bắn',110),('series','Loạt',70),('lane','Bệ',70),('after','Ảnh sau',130)],8)
        for p in self.store.list_drafts():tree.insert('','end',iid=p['id'],values=[p.get('shooter'),p.get('unit'),shooting_day(p),p.get('series'),p.get('lane'),'Đã có' if p['has_after'] else 'Chưa chụp'])
        def restore():
            selected=tree.selection()
            if len(selected)!=1:raise ValueError('Chọn một loạt nháp')
            ident=selected[0];p,images=self.store.load_draft(ident);config=p.get('_draft_config') or self.default_config
            self.check_template(p,config)
            if not self.may_replace_round():return
            self.clear_round();self.config=copy.deepcopy(config);self.fill_identity(p);self.meta=p;self.meta.pop('draft_archived',None);self.round_id=ident
            if self.cap:self.cap.release();self.cap=None;self.frame=None
            self.before_image=images['before'];self.after_image=images['after']
            for i,n in enumerate(('before','after')):
                if images[n] is not None:self.show(self.views[i],images[n])
            self.summary.set('Đã khôi phục ảnh nháp. '+('Bấm Tính lại / thử lại để xử lý.' if self.after_image is not None else 'Chụp ảnh sau khi kết thúc loạt.'))
            self.notebook.select(0);window.destroy()
        def archive():
            selected=tree.selection()
            if not selected:raise ValueError('Chọn loạt muốn ẩn khỏi danh sách khôi phục')
            if self.round_id in selected:raise ValueError('Loạt đang mở; chuyển sang loạt khác trước khi ẩn nháp')
            for ident in selected:self.store.archive_draft(ident);tree.delete(ident)
        bar=ttk.Frame(window);bar.pack(fill='x',padx=8,pady=8)
        self.button(bar,'Khôi phục loạt đã chọn',restore).pack(side='left',padx=4);self.button(bar,'Ẩn nháp (giữ ảnh trên đĩa)',archive).pack(side='left',padx=4)
        tree.bind('<Double-1>',lambda e:self.guard(restore))
