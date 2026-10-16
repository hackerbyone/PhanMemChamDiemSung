"""Opt-in Windows/Tk workflow checks: RUN_DESKTOP_TESTS=1."""
import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'edge_real'))
from storage import Store
from core import read_image,sha


@unittest.skipUnless(os.environ.get('RUN_DESKTOP_TESTS')=='1','Set RUN_DESKTOP_TESTS=1 on a desktop with Tcl/Tk')
class DesktopWorkflow(unittest.TestCase):
    def setUp(self):
        import app
        self.module=app;self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'data')
        self.config=json.loads((ROOT/'target_config.json').read_text(encoding='utf8'));self.config['confirmed']=True
        def immediate(instance,func,done,text=''):done(func())
        self.patches=[patch.object(app,'Store',return_value=self.store),patch.object(app,'load_or_extract',return_value=copy.deepcopy(self.config)),patch.object(app.App,'task',immediate),patch.object(app.messagebox,'askyesno',return_value=True),patch.object(app.messagebox,'showerror',side_effect=lambda title,msg,**kw:(_ for _ in ()).throw(AssertionError(msg)))]
        for p in self.patches:p.start()
        self.addCleanup(self.cleanup)
        self.app=app.App();self.app.withdraw();self.app.update_idletasks()
        self.im=read_image(ROOT/'assets/Biaso4.png')
        self.meta={'shooter_code':'S01','shooter':'Nguyễn Văn An','unit':'Đơn vị A','lane':2,'series':1,'shooting_date':'2026-10-15','expected_shots':1,'_template_sha256':sha(ROOT/'assets/Biaso4.png')}
        self.result={'status':'AUTO','warnings':[],'config':self.config,'detections':[{'template_point':self.config['center'],'score':10,'selected':True,'confidence':.9,'source':'model','matched_before':None,'issues':[]}],'homography_before':np.eye(3).tolist(),'homography_after':np.eye(3).tolist()}
    def cleanup(self):
        if hasattr(self,'app'):
            for job in self.app.tk.call('after','info'):self.app.after_cancel(job)
            self.app.destroy()
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    def seed(self):
        _,p=self.store.save(self.result,self.meta,self.im,self.im,self.im);self.app.refresh_history();self.app.history_table.selection_set(p['id']);self.app.open_history();return p

    def test_history_open_edit_review_and_next_shooter(self):
        p=self.seed();self.assertEqual(self.app.vars['shooting_date'].get(),'2026-10-15');self.assertEqual(self.app.expected_revision,1)
        self.app.tree.selection_set('0');self.app.toggle();self.assertEqual(self.store.load_payload(p['id'])['total_score'],0)
        self.app.review();self.assertEqual(self.store.load_payload(p['id'])['status'],'REVIEWED')
        self.app.tree.selection_set('0');self.app.toggle();self.assertNotIn('reviewed_at',self.store.load_payload(p['id']))
        self.assertEqual(self.store.load_payload(p['id'])['revision'],4)
        self.app.reset(new_shooter=True);self.assertEqual(self.app.vars['shooter'].get(),'');self.assertEqual(self.app.vars['series'].get(),'1')
        self.assertIsNone(self.app.expected_revision);self.assertEqual(len(self.store.list_rounds()),1)

    def test_inspection_modes_selection_zoom_and_roster_choice(self):
        self.seed();self.app.open_inspection();viewer=self.app.inspector;viewer.withdraw();viewer.update_idletasks()
        for mode in ('Ảnh trước nguyên gốc','Ảnh sau nguyên gốc','Ảnh sau đã căn','Bia mẫu'):
            viewer.mode.set(mode);viewer.render(True);self.assertEqual(len(viewer.display_points),1)
        viewer.table.selection_set('0');viewer.select();self.assertEqual(self.app.tree.selection(),('0',))
        old=viewer.zoom.scale;viewer.zoom.zoom(1.2);self.assertGreater(viewer.zoom.scale,old)
        self.store.save_shooters([{'code':'S02','name':'Trần Bình','unit':'Đội B'}]);self.app.refresh_roster();self.app.roster_table.selection_set('S02');self.app.choose_shooter()
        self.assertEqual(self.app.vars['shooter_code'].get(),'S02');self.assertEqual(self.app.vars['unit'].get(),'Đội B');self.assertIsNone(self.app.result)

    def test_restore_draft_keeps_identity_and_both_images(self):
        import uuid
        from tkinter import ttk
        ident=str(uuid.uuid4());meta=dict(self.meta,_draft_config=self.config,before_acquired_at='2026-10-15T02:00:00+00:00')
        self.store.capture(ident,'before',self.im,meta);self.store.capture(ident,'after',self.im,meta)
        self.app.drafts_window();window=[w for w in self.app.winfo_children() if w.winfo_class()=='Toplevel'][-1];window.withdraw()
        def descendants(widget):
            for child in widget.winfo_children():yield child;yield from descendants(child)
        tree=next(w for w in descendants(window) if isinstance(w,ttk.Treeview));tree.selection_set(ident)
        next(w for w in descendants(window) if isinstance(w,ttk.Button) and w.cget('text')=='Khôi phục loạt đã chọn').invoke()
        self.assertEqual(self.app.round_id,ident);self.assertFalse(self.app.saved);self.assertEqual(self.app.meta['shooter_code'],'S01')
        self.assertIsNotNone(self.app.after_image);self.assertEqual(self.app.vars['shooting_date'].get(),'2026-10-15')


if __name__=='__main__':unittest.main(verbosity=2)
