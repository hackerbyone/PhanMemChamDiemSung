"""Desktop regressions using isolated data, no camera or server."""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np

from annotations import markers,hole_labels,draw_markers
from core import read_image
from storage import Store
from template_geometry import load_or_extract

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get('RUN_DESKTOP_TESTS')=='1','Set RUN_DESKTOP_TESTS=1')
class DesktopLayout(unittest.TestCase):
    def setUp(self):
        import app
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'sample_check');self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'data')
        self.config=load_or_extract(ROOT/'assets/Biaso4.png',Path(self.tmp.name)/'config.json')
        self.patches=[patch.object(app,'Store',return_value=self.store),patch.object(app,'load_or_extract',return_value=copy.deepcopy(self.config)),patch.object(app.App,'task',lambda instance,func,done,text='':done(func())),patch.object(app.messagebox,'askyesno',side_effect=AssertionError('Unexpected confirmation')),patch.object(app.messagebox,'showerror',side_effect=lambda *a,**kw:(_ for _ in ()).throw(AssertionError(a)))]
        for p in self.patches:p.start();self.addCleanup(p.stop)
        self.app=app.App();self.app.withdraw();self.addCleanup(self.destroy)
        self.app.update_idletasks()
    def destroy(self):
        for job in self.app.tk.call('after','info'):self.app.after_cancel(job)
        self.app.destroy()
    def seed(self):
        im=read_image(ROOT/'assets/Biaso4.png');self.app.before_image=im;self.app.after_image=im.copy()
        self.app.result={'config':self.config,'before_detections':[dict(template_point=[400,400],confidence=.91)],'detections':[dict(template_point=[400,400],matched_before=0,confidence=.93,selected=False,score=10),dict(template_point=[500,500],matched_before=None,confidence=.82,selected=True,score=10)],'homography_before':np.eye(3).tolist(),'homography_after':np.eye(3).tolist(),'status':'AUTO','warnings':[]}
        self.app.saved=True;self.app.refresh()
    def test_startup_two_images_no_confirmation_or_roster_popup(self):
        self.assertEqual(len(self.app.views),2)
        self.assertEqual(set(self.app.original_buttons),{'before','after'})
        self.assertEqual([self.app.original_buttons[k].cget('text') for k in ('before','after')],['Xem ảnh gốc','Xem ảnh gốc'])
        self.assertEqual(self.app.inspection_panel.winfo_manager(),'')
        self.assertTrue(self.app.queue_table.winfo_exists())
        self.assertTrue(self.app.config['confirmed'])
        self.assertEqual(self.app.roster_window.state(),'withdrawn')
        self.assertEqual(len(self.app.notebook.tabs()),3)
    def test_matching_labels_colors_and_zoom_selection(self):
        self.seed();self.assertEqual(self.app.tree.item('0','values')[0],'C1');self.assertEqual(self.app.tree.item('1','values')[0],'M1')
        self.app.open_image('before');window=self.app.zoom_windows['before'];window.withdraw();window.select_hole([400,400])
        self.assertEqual(self.app.tree.selection(),('0',))
        self.app.open_image('after');window=self.app.zoom_windows['after'];window.withdraw();window.select_hole([500,500])
        self.assertEqual(self.app.tree.selection(),('1',))
        old=window.zoom.scale;window.zoom.zoom(1.2);self.assertGreater(window.zoom.scale,old)
        self.assertEqual([v['old'] for v in self.app.image_items('after')],[True,False])
    def test_roster_search_detail_and_choose(self):
        self.store.save_shooters([dict(code='S01',name='Nguyễn Văn An',unit='Đội A'),dict(code='S02',name='Trần Bình',unit='Đội B')])
        self.app.roster_query.set('S02');self.app.open_roster();self.app.roster_window.withdraw()
        self.assertEqual(self.app.roster_table.get_children(),('S02',))
        self.app.roster_table.selection_set('S02');self.app.open_shooter_detail();self.app.choose_shooter()
        self.assertEqual(self.app.vars['shooter'].get(),'Trần Bình');self.assertEqual(self.app.roster_window.state(),'withdrawn')
    def test_before_image_can_zoom_without_result(self):
        self.app.before_image=read_image(ROOT/'assets/Biaso4.png');self.app.before_preview=[dict(point=[400,400],label='C1',old=True,index=0,confidence=.9)]
        self.app.open_image('before');window=self.app.zoom_windows['before'];window.update()
        self.assertTrue(window.winfo_exists());self.assertGreater(window.zoom.scale,.4)
    def test_before_detection_populates_confidence_rows(self):
        self.app.before_image=read_image(ROOT/'assets/Biaso4.png')
        class Detector:
            def detect(self,image,confidence):return [dict(point=[400,400],confidence=.91)]
        self.app.detector=Detector()
        with patch('app.align',return_value=(np.eye(3),{})):self.app.detect_before()
        self.assertEqual(self.app.tree.item('0','values')[0],'C1')
        self.assertEqual(self.app.tree.item('0','values')[3],'91%')
        self.assertTrue(self.app.before_preview[0]['old'])
    def test_remove_add_after_coordinates_and_saved_totals(self):
        import uuid
        self.seed();self.app.meta=dict(shooter_code='DEMO',shooter='Demo',unit='Demo',lane=1,series=1,demo=True)
        self.app.round_id=str(uuid.uuid4());self.app.persist()
        self.app.tree.selection_set('1');self.app.toggle()
        self.assertEqual(self.app.result['total_score'],0)
        self.assertEqual(self.app.tree.item('1','values')[2],'0')
        self.assertEqual([m['index'] for m in self.app.image_items('after')],[0])
        self.app.result['homography_after']=[[1,0,100],[0,1,50],[0,0,1]]
        self.app.set_manual_hole([351,403])
        self.assertEqual(self.app.result['detections'][-1]['template_point'],[451,453])
        self.assertEqual(self.app.result['total_score'],10)
        self.app.set_manual_hole([0,0])
        self.assertEqual(self.app.result['detections'][-1]['score'],0)
        self.assertEqual(self.app.result['total_score'],10)
        payload=self.store.load_payload(self.app.round_id)
        self.assertEqual(payload['total_score'],10);self.assertEqual(payload['revision'],4)
    def test_after_preview_from_main_tab_is_readonly(self):
        from types import SimpleNamespace
        self.seed()
        self.app.open_image('after');window=self.app.zoom_windows['after'];window.update()
        self.assertFalse(window.editable)
        self.assertIn('chỉ xem',window.title().lower())
        zoom=window.zoom;zoom.scale=1.;zoom.draw();zoom.canvas.xview_moveto(0);zoom.canvas.yview_moveto(0)
        with patch.object(self.app,'set_manual_hole') as save:
            window.select_hole([500,500])
            self.assertEqual(self.app.tree.selection(),('1',))
            self.assertIsNone(window.hole_dialog)
            start=SimpleNamespace(x=150,y=160);end=SimpleNamespace(x=200,y=200)
            zoom.down(start);zoom.pan(end);zoom.up(end)
            zoom.right_down(SimpleNamespace(x=500,y=500));zoom.right_pan(end);zoom.right_up(end)
            save.assert_not_called()
    def test_drag_circle_uses_zoomed_scrolled_coordinates(self):
        from types import SimpleNamespace
        self.seed();self.app.open_image('after',editable=True);window=self.app.zoom_windows['after'];window.update()
        zoom=window.zoom;zoom.scale=2.;zoom.draw();zoom.canvas.xview_moveto(.1);zoom.canvas.yview_moveto(.1)
        start=SimpleNamespace(x=150,y=160);end=SimpleNamespace(x=190,y=160)
        expected=[zoom.canvas.canvasx(start.x)/2,zoom.canvas.canvasy(start.y)/2]
        with patch.object(self.app,'set_manual_hole') as save:
            zoom.down(start);zoom.pan(end);zoom.up(end)
            self.assertEqual(save.call_count,1)
            np.testing.assert_allclose(save.call_args.args[0],expected)
            self.assertAlmostEqual(save.call_args.kwargs['radius'],20)
            save.reset_mock();zoom.down(start);zoom.up(start)
            save.assert_not_called()
            zoom.down(start);zoom.pan(end);window.cancel_edit();zoom.up(end);save.assert_not_called()
    def test_click_dialog_deletes_and_updates_saved_score(self):
        import uuid
        from tkinter import ttk
        self.seed();self.app.meta=dict(shooter_code='DEMO',shooter='Demo',unit='Demo',lane=1,series=1,demo=True)
        self.app.round_id=str(uuid.uuid4());self.app.persist()
        self.app.open_image('after',editable=True);window=self.app.zoom_windows['after'];window.select_hole([500,500])
        dialog=window.hole_dialog;self.assertTrue(dialog.winfo_exists())
        buttons=[child for frame in dialog.winfo_children() for child in frame.winfo_children() if isinstance(child,ttk.Button)]
        next(button for button in buttons if button.cget('text')=='Xóa điểm này').invoke()
        self.assertFalse(dialog.winfo_exists());self.assertEqual(self.app.result['total_score'],0)
        self.assertEqual([item['index'] for item in self.app.image_items('after')],[0])
        self.assertEqual(self.store.load_payload(self.app.round_id)['total_score'],0)
    def test_move_existing_circle_at_zoom_without_adding_hole(self):
        import uuid
        from types import SimpleNamespace
        self.seed();self.app.meta=dict(shooter_code='DEMO',shooter='Demo',unit='Demo',lane=1,series=1,demo=True)
        self.app.round_id=str(uuid.uuid4());self.app.persist()
        self.app.open_image('after',editable=True);window=self.app.zoom_windows['after'];window.update()
        zoom=window.zoom;zoom.scale=2.;zoom.draw();zoom.canvas.xview_moveto(.15);zoom.canvas.yview_moveto(.15)
        sx=500*2-zoom.canvas.canvasx(0);sy=500*2-zoom.canvas.canvasy(0)
        start=SimpleNamespace(x=int(sx),y=int(sy));end=SimpleNamespace(x=int(sx-800),y=int(sy-800))
        zoom.right_down(start);self.assertEqual(zoom.moving['index'],1);zoom.right_pan(end);zoom.right_up(end)
        self.assertEqual(len(self.app.result['detections']),2)
        np.testing.assert_allclose(self.app.result['detections'][1]['original_point'],[100,100])
        self.assertEqual(self.app.result['total_score'],0)
        self.assertEqual(self.app.tree.item('1','values')[0],'M1')
        self.assertEqual(self.app.tree.item('1','values')[2],'0')
        self.assertEqual(self.store.load_payload(self.app.round_id)['total_score'],0)
    def test_right_click_and_empty_drag_do_not_add_or_save(self):
        from types import SimpleNamespace
        self.seed();self.app.open_image('after',editable=True);window=self.app.zoom_windows['after'];window.update()
        zoom=window.zoom;zoom.scale=1.;zoom.draw();zoom.canvas.xview_moveto(0);zoom.canvas.yview_moveto(0)
        hit=SimpleNamespace(x=500,y=500);empty=SimpleNamespace(x=50,y=50);end=SimpleNamespace(x=90,y=90)
        with patch.object(self.app,'set_manual_hole') as save:
            zoom.right_down(hit);zoom.right_up(hit);save.assert_not_called()
            zoom.right_down(empty);zoom.right_pan(end);zoom.right_up(end);save.assert_not_called()
            zoom.right_down(hit);window.cancel_edit();zoom.right_up(end);save.assert_not_called()
    def test_change_color_relabels_table_and_saved_results(self):
        import uuid
        from tkinter import ttk
        self.seed();self.app.meta=dict(shooter_code='DEMO',shooter='Demo',unit='Demo',lane=1,series=1,demo=True)
        self.app.round_id=str(uuid.uuid4());self.app.persist();self.app.set_manual_hole([451,453],radius=16)
        self.app.open_image('after',editable=True);window=self.app.zoom_windows['after'];window.open_hole_dialog(1)
        buttons=[child for frame in window.hole_dialog.winfo_children() for child in frame.winfo_children() if isinstance(child,ttk.Button)]
        next(button for button in buttons if button.cget('text')=='Đỏ — Lỗ cũ').invoke()
        self.assertEqual(hole_labels(self.app.result['detections']),['C1','C2','M1'])
        self.assertEqual(self.app.result['total_score'],10)
        self.assertEqual(self.app.tree.item('1','values')[0],'C2')
        self.assertTrue(self.app.image_items('after')[1]['old'])
        self.app.set_manual_hole([450,453],1,radius=15)
        self.assertEqual(self.app.result['detections'][1]['classification'],'old')
        self.assertEqual(self.app.result['total_score'],10)
        self.app.classify_hole(1,'new')
        self.assertEqual(hole_labels(self.app.result['detections']),['C1','M1','M2'])
        self.assertEqual(self.app.result['total_score'],20)
        self.app.remove_hole(1)
        self.assertEqual(hole_labels(self.app.result['detections']),['C1','—','M1'])
        self.assertEqual(self.app.tree.item('2','values')[0],'M1')
        payload=self.store.load_payload(self.app.round_id)
        self.assertEqual(hole_labels(payload['detections']),['C1','—','M1'])
        self.assertEqual(payload['total_score'],10)


class AnnotationCoordinates(unittest.TestCase):
    def test_inverse_projection_and_new_numbering(self):
        r={'before_detections':[dict(template_point=[30,40])],'detections':[dict(template_point=[30,40],matched_before=0,score=0),dict(template_point=[50,60],matched_before=None,score=0)],'homography_before':[[1,0,10],[0,1,20],[0,0,1]],'homography_after':[[1,0,10],[0,1,20],[0,0,1]]}
        self.assertEqual(hole_labels(r['detections']),['C1','M1'])
        items=markers(r,'after');self.assertEqual(items[0]['point'],[20,20])
        image=np.zeros((100,100,3),np.uint8);annotated=draw_markers(image,items)
        self.assertEqual(int(image.sum()),0);self.assertGreater(int(annotated[:,:,2].sum()),0);self.assertGreater(int(annotated[:,:,1].sum()),0)


if __name__=='__main__':unittest.main()
