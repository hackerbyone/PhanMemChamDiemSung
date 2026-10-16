import copy
import json
import sqlite3
import sys
import tempfile
import unittest
import uuid
from contextlib import closing
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'edge_real'))
from storage import Store,shooting_day
from reports import export_xlsx,print_report,import_roster,roster_template


class ManagementData(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.store=Store(self.root/'data')
        self.im=np.full((200,200,3),255,np.uint8)
        self.meta={'shooter_code':'S01','shooter':'Nguyễn Văn An','unit':'Đơn vị A','lane':2,'series':1,'shooting_date':'2026-10-15','expected_shots':1}
        self.result={'status':'AUTO','warnings':[],'config':{'center':[100,100],'radii':{'10':10,'9':20,'8':30,'7':40,'6':50},'outline':[[20,20],[180,20],[180,180],[20,180]],'confirmed':True},'detections':[{'template_point':[100,100],'score':10,'selected':True,'source':'model','confidence':.9}],'homography_before':np.eye(3).tolist(),'homography_after':np.eye(3).tolist()}
    def tearDown(self):self.tmp.cleanup()
    def save(self,meta=None,**kwargs):return self.store.save(self.result,meta or self.meta,self.im,self.im,self.im,**kwargs)

    def test_migrates_original_database_without_changing_payload_or_sync(self):
        root=self.root/'legacy';root.mkdir();ident=str(uuid.uuid4())
        p=dict(self.meta,created_at='2026-10-14T19:00:00+00:00',detections=[],total_score=0,total_holes=0,status='AUTO');p.pop('shooting_date')
        raw=json.dumps(p)
        with closing(sqlite3.connect(root/'rounds.db')) as c,c:
            c.execute('CREATE TABLE rounds(id TEXT PRIMARY KEY,payload TEXT NOT NULL,synced INTEGER DEFAULT 0)');c.execute('INSERT INTO rounds VALUES(?,?,1)',(ident,raw))
        migrated=Store(root);row=migrated.list_rounds()[0]
        self.assertEqual(row['shooting_date'],'2026-10-15');self.assertEqual(row['shooter'],'Nguyễn Văn An');self.assertEqual(row['synced'],1)
        with closing(sqlite3.connect(migrated.db)) as c:self.assertEqual(c.execute('SELECT payload FROM rounds').fetchone()[0],raw)
        self.assertEqual(migrated.list_shooters()[0]['code'],'S01');self.assertEqual(len(Store(root).list_rounds()),1)

    def test_filter_unicode_day_lane_status_and_open_latest_revision(self):
        folder,p=self.save();self.save(dict(self.meta,shooter_code='S02',shooter='Trần Bình',shooting_date='2026-10-16',lane=1))
        found=self.store.list_rounds(query='NGUYỄN',day_from='2026-10-15',day_to='2026-10-15',lane='2',status='AUTO')
        self.assertEqual([r['id'] for r in found],[p['id']]);self.assertEqual(len(self.store.list_rounds(query='đơn vị')),2)
        self.assertEqual(len(self.store.list_rounds(query='nguyen van an')),1)
        _,updated=self.save(ident=p['id'],expected_revision=1)
        payload,images=self.store.load_round(p['id']);self.assertEqual(payload['revision'],2);np.testing.assert_array_equal(images['before'],self.im)
        self.assertTrue((folder/'rev_1/result.json').exists());self.assertEqual(len(self.store.list_rounds()),2)
        with self.assertRaises(ValueError):self.save(ident=p['id'],expected_revision=1)
        self.assertEqual(self.store.load_payload(p['id'])['revision'],2)

    def test_roster_bulk_conflict_rolls_back_and_does_not_rewrite_history(self):
        self.store.save_shooters([{'code':'S01','name':'Nguyễn Văn An','unit':'Đơn vị A'}]);_,p=self.save()
        with self.assertRaises(ValueError):self.store.save_shooters([{'code':'S02','name':'B','unit':'U'},{'code':'S01','name':'Changed','unit':'U'}])
        self.assertEqual(len(self.store.list_shooters()),1)
        self.store.save_shooters([{'code':'S01','name':'Tên mới','unit':'Đơn vị mới'}],replace=True)
        self.assertEqual(self.store.load_payload(p['id'])['shooter'],'Nguyễn Văn An')
        self.assertEqual(self.store.list_shooters('TÊN MỚI')[0]['code'],'S01')

    def test_draft_recovery_before_only_then_both_and_saved_hidden(self):
        ident=str(uuid.uuid4());meta=dict(self.meta,before_acquired_at='2026-10-15T02:00:00+00:00',_draft_config=self.result['config'])
        self.store.capture(ident,'before',self.im,meta)
        rows=Store(self.store.root).list_drafts();self.assertEqual(rows[0]['id'],ident);self.assertFalse(rows[0]['has_after'])
        payload,images=self.store.load_draft(ident);self.assertIsNone(images['after']);self.assertEqual(payload['_draft_config'],self.result['config'])
        self.store.capture(ident,'after',self.im,meta);self.assertTrue(self.store.list_drafts()[0]['has_after'])
        self.save(ident=ident);self.assertEqual(self.store.list_drafts(),[])
        ident2=str(uuid.uuid4());self.store.capture(ident2,'before',self.im,meta);self.store.archive_draft(ident2)
        self.assertEqual(self.store.list_drafts(),[]);self.assertTrue((self.store.root/'drafts'/ident2/'before.png').exists())

    def test_csv_bom_vietnamese_headers_and_atomic_validation(self):
        path=self.root/'roster.csv';path.write_text('Mã người bắn;Họ tên;Đơn vị\nS03;Lê An;Đội B\n',encoding='utf-8-sig')
        self.assertEqual(self.store.save_shooters(import_roster(path)),1)
        self.assertEqual(self.store.list_shooters()[0]['name'],'Lê An')
        template=self.root/'template.csv';roster_template(template);self.assertEqual(import_roster(template)[0]['code'],'S01')
        with self.assertRaises(ValueError):self.store.save_shooters([{'code':'S04','name':'A','unit':''}])
        self.assertEqual(len(self.store.list_shooters()),1)

    def test_xlsx_readable_numeric_totals_unicode_and_no_formula_execution(self):
        _,p=self.save(dict(self.meta,shooter='=HYPERLINK("bad")'))
        demo=copy.deepcopy(p);demo['status']='DEMO';demo['demo']=True
        path=self.root/'results.xlsx';export_xlsx(path,[p,demo])
        with ZipFile(path) as z:
            self.assertIsNone(z.testzip())
            for name in z.namelist():ET.fromstring(z.read(name))
            xml=z.read('xl/worksheets/sheet1.xml').decode();self.assertIn('Đơn vị A',xml);self.assertNotIn('<f>',xml)
        try:from openpyxl import load_workbook
        except ImportError:return
        workbook=load_workbook(path,read_only=True,data_only=False)
        try:
            self.assertEqual(workbook.sheetnames,['Loạt bắn','Theo người','Theo đơn vị','Chi tiết lỗ'])
            self.assertEqual(workbook['Loạt bắn']['C2'].data_type,'s');self.assertEqual(workbook['Loạt bắn']['J2'].value,10)
            summary=list(workbook['Theo người'].values);self.assertEqual(len(summary),3)
            self.assertEqual({r[3] for r in summary[1:]},{'DEMO','Thực tế (gồm chưa duyệt)'})
        finally:workbook.close()

    def test_print_report_embeds_image_escapes_names_and_preserves_status(self):
        _,p=self.save(dict(self.meta,shooter='<script>alert(1)</script>'))
        path=self.root/'print.html';print_report(path,[p],self.store);html=path.read_text(encoding='utf8')
        self.assertIn('&lt;script&gt;',html);self.assertNotIn('<script>',html);self.assertIn('data:image/png;base64,',html)
        self.assertIn('Tự động - chưa duyệt',html);self.assertIn('2026-10-15',html);self.assertIn('window.print()',html)

    def test_day_uses_vietnam_timezone_and_validates_filters(self):
        self.assertEqual(shooting_day({'created_at':'2026-10-14T18:00:00Z'}),'2026-10-15')
        with self.assertRaises(ValueError):self.store.list_rounds(day_from='2026-02-30')
        with self.assertRaises(ValueError):self.store.list_rounds(day_from='2026-10-16',day_to='2026-10-15')


if __name__=='__main__':unittest.main(verbosity=2)
