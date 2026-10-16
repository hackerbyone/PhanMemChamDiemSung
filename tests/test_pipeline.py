import base64
import copy
import json
import sqlite3
import sys
import tempfile
import unittest
import uuid
from contextlib import closing
from pathlib import Path
import numpy as np
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'edge_real'),str(ROOT/'server')]
from core import analyze,score_point,match_holes,align,transform,read_image,validate_config
from storage import Store
from template_geometry import extract_geometry
from real_api import install
C={'center':[100,100],'radii':{'10':10,'9':20,'8':30,'7':40,'6':50},'outline':[[20,20],[180,20],[180,180],[20,180]],'confirmed':True}
class Geometry(unittest.TestCase):
    def test_mask_before_rings(self):
        c=copy.deepcopy(C);c['outline']=[[100,50],[150,100],[100,150]]
        self.assertEqual(score_point([99,100],c),0)
    def test_all_bands_and_boundary(self):
        for d,s in [(0,10),(10,10),(10.001,9),(20,9),(21,8),(31,7),(41,6),(51,5),(81,0)]:
            self.assertEqual(score_point([100+d,100],C),s)
    def test_one_to_one(self):
        matches,missing=match_holes([[1,1]],[[1,1],[2,1]],5)
        self.assertEqual(matches.count(None),1);self.assertEqual(missing,[])
    def test_lost_old(self):
        self.assertEqual(match_holes([[1,1],[30,30]],[[1,1]],5),([0],[1]))
    def test_bad_calibration(self):
        c=copy.deepcopy(C);c['radii']['9']=5
        with self.assertRaises(ValueError):validate_config(c)
    def test_degenerate_homography(self):
        im=np.zeros((200,200,3),np.uint8)
        with self.assertRaises(ValueError):align(im,im,([[0,0],[1,0],[2,0],[3,0]],[[0,0],[1,0],[2,0],[3,0]]))
    def test_manual_known_transform(self):
        im=np.zeros((200,200,3),np.uint8);p=[[10,10],[160,10],[160,160],[10,160]];q=[[20,20],[170,20],[170,170],[20,170]]
        h,_=align(im,im,(p,q));np.testing.assert_allclose(transform([[50,50]],h),[[60,60]],atol=1e-5)
    def test_sample_alignment(self):
        h,q=align(read_image(ROOT/'assets/2.Ket_thuc_New68.bmp'),read_image(ROOT/'assets/Biaso4.png'))
        self.assertLess(q['rmse_px'],4);self.assertGreater(q['inliers'],10)
class Integration(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        app=FastAPI()
        from fastapi import Header
        def auth(x_manager_token:str|None=Header(default=None)):
            if x_manager_token!='test':raise HTTPException(401)
            return True
        install(app,auth,self.root/'server.db',self.root/'images');self.client=TestClient(app)
        self.p={'id':str(uuid.uuid4()),'created_at':'2026-01-01','reviewed_at':'2026-01-01','shooter_code':'S1','shooter':'Test','unit':'U','lane':1,'series':1,'config':C,'detections':[{'template_point':[100,100],'score':10,'selected':True}],'total_score':10,'total_holes':1}
        b=base64.b64encode((ROOT/'assets/Biaso4.png').read_bytes()).decode();self.body={'result':self.p,'images':dict.fromkeys(['before','after','annotated'],b)}
    def tearDown(self):self.client.close();self.tmp.cleanup()
    def post(self):return self.client.post('/api/real-rounds',json=self.body,headers={'X-Manager-Token':'test'})
    def test_requires_auth(self):self.assertEqual(self.client.post('/api/real-rounds',json=self.body).status_code,401)
    def test_roundtrip_retry_and_conflict(self):
        self.assertEqual(self.post().status_code,200);self.assertTrue(self.post().json()['duplicate'])
        self.assertEqual(len(self.client.get('/api/real-rounds').json()),1)
        r=self.client.get('/api/real-rounds/'+self.p['id']).json();self.assertEqual(r['total_score'],10)
        self.p['shooter']='Changed';self.assertEqual(self.post().status_code,409)
    def test_score_tampering(self):
        self.p['detections'][0]['score']=9;self.p['total_score']=9
        self.assertEqual(self.post().status_code,422)
    def test_bad_image(self):
        self.body['images']['before']='bad base64';self.assertEqual(self.post().status_code,422)
    def test_zero_holes_keeps_images(self):
        self.p.update(detections=[],total_score=0,total_holes=0)
        self.assertEqual(self.post().status_code,200)
        self.assertTrue((self.root/'images'/'real'/self.p['id']/'rev_1'/'after.png').exists())
    def test_local_persistence(self):
        store=Store(self.root/'local');im=read_image(ROOT/'assets/Biaso4.png')
        p,payload=store.save({'detections':self.p['detections']},{'shooter':'Test'},im,im,im)
        with closing(sqlite3.connect(store.db)) as c:self.assertEqual(c.execute('SELECT synced FROM rounds').fetchone()[0],0)
        self.assertTrue((p/'rev_1'/'before.png').exists());self.assertTrue((p/'result.json').exists())
class Automatic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template=read_image(ROOT/'assets/Biaso4.png');cls.config=extract_geometry(cls.template)
    def test_detects_geometry_from_pixels(self):
        np.testing.assert_allclose(self.config['center'],[451,453],atol=3)
        np.testing.assert_allclose(list(self.config['radii'].values()),[99,199,299,399,498],atol=3)
        self.assertEqual(score_point([450,453],self.config),10)
        self.assertEqual(score_point([750,200],self.config),0)
        self.assertEqual(score_point([70,840],self.config),5)
        # Bright area in the face remains inside the silhouette.
        self.assertGreater(score_point([355,340],self.config),0)
    def test_blank_template_rejected(self):
        with self.assertRaises(ValueError):extract_geometry(np.full((500,500,3),255,np.uint8))
    def test_warp_before_detection_and_subtract_old(self):
        template=np.full((200,200,3),127,np.uint8);source=np.full((300,300,3),127,np.uint8)
        pairs=([[0,0],[299,0],[299,299],[0,299]],[[0,0],[199,0],[199,199],[0,199]])
        class Fake:
            def __init__(self):self.calls=0
            def detect(self,im,conf):
                self.calls+=1
                if im.shape!=(200,200,3):raise AssertionError('Detector must receive rectified images')
                pts=[[100,100]] if self.calls==1 else [[101,100],[130,100]]
                return [{'point':p,'box':[p[0]-3,p[1]-3,p[0]+3,p[1]+3],'confidence':.9,'class_id':0} for p in pts]
        r=analyze(source,source,template,C,Fake(),pairs=[pairs,pairs])
        self.assertEqual([h['selected'] for h in r['detections']],[False,True])
        self.assertEqual(r['detections'][1]['score'],8)
    def test_partial_target_rejected(self):
        image=np.full((200,200,3),127,np.uint8)
        pairs=([[0,0],[199,0],[199,199],[0,199]],[[100,0],[299,0],[299,199],[100,199]])
        class MustNotRun:
            def detect(self,*args):raise AssertionError('Must reject before detection')
        with self.assertRaises(ValueError):analyze(image,image,image,C,MustNotRun(),pairs=[pairs,pairs])
    def test_local_revisions_keep_evidence(self):
        with tempfile.TemporaryDirectory() as t:
            st=Store(t);im=self.template;result={'detections':[]};p,a=st.save(result,{},im,im,im)
            p,b=st.save(result,{},im,im,im,a['id'])
            self.assertEqual(b['revision'],2);self.assertTrue((p/'rev_1'/'after.png').exists())
            with closing(sqlite3.connect(st.db)) as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM rounds').fetchone()[0],1)

class Revisions(unittest.TestCase):
    setUp=Integration.setUp
    tearDown=Integration.tearDown
    post=Integration.post
    def test_upload_correction_and_reject_stale(self):
        self.p.update(status='AUTO',revision=1);self.p.pop('reviewed_at',None)
        self.assertEqual(self.post().status_code,200)
        self.p.update(status='REVIEWED',reviewed_at='2026-01-02',revision=2)
        self.p['detections'][0].update(template_point=[130,100],score=8);self.p['total_score']=8
        self.assertEqual(self.post().status_code,200);self.assertTrue(self.post().json()['duplicate'])
        with closing(sqlite3.connect(self.root/'server.db')) as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM real_round_history').fetchone()[0],1)
        self.p['revision']=1;self.assertEqual(self.post().status_code,409)
    def test_revision_cannot_move_shooter(self):
        self.assertEqual(self.post().status_code,200);self.p.update(revision=2,shooter='Other')
        self.assertEqual(self.post().status_code,409)
    def test_shooting_date_preserved_on_correction(self):
        self.p.update(shooting_date='2026-10-15',notes='Kiểm tra buổi sáng')
        self.assertEqual(self.post().status_code,200)
        self.assertEqual(self.client.get('/api/real-rounds').json()[0]['shooting_date'],'2026-10-15')
        self.p.update(revision=2,shooting_date='2026-10-16');self.assertEqual(self.post().status_code,409)
    def test_invalid_shooting_date_rejected(self):
        self.p['shooting_date']='2026-02-30';self.assertEqual(self.post().status_code,422)

if __name__=='__main__':unittest.main(verbosity=2)

