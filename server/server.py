import os, sqlite3, uuid, json, base64, binascii, secrets
from datetime import datetime, timezone
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE=os.path.dirname(os.path.abspath(__file__))
DB=os.path.join(BASE,"server.db")
SCHEMA=os.path.join(os.path.dirname(BASE),"schema.sql")
IMAGES_DIR=os.path.join(BASE,"images")
os.makedirs(IMAGES_DIR, exist_ok=True)

BUILD_TAG="sqlite-v10-admin-reset-2026-09-15"  # bump this whenever you deploy, then check /api/health

# Simple shared-password manager login (see /api/manager/login). Change this
# via the MANAGER_PASSWORD environment variable for anything beyond a demo.
MANAGER_PASSWORD=os.environ.get("MANAGER_PASSWORD","admin123")
manager_tokens=set()  # valid bearer tokens; in-memory, reset on server restart

# Score classification bands applied to each individual shot's score
# (0-10 scale). Used by /api/stats/* below. Change here if your scoring
# scale or desired thresholds differ.
SCORE_BANDS = [("EXCELLENT", 9.0), ("GOOD", 7.0), ("OK", 5.0)]  # else FAIL

app=FastAPI(title="Shooting Scoring Server", version="3.1")
app.add_middleware(CORSMiddleware,allow_origins=["*"],allow_methods=["*"],allow_headers=["*"])
app.mount("/images", StaticFiles(directory=IMAGES_DIR), name="images")
clients=set()

def now(): return datetime.now(timezone.utc).isoformat()
def U(): return str(uuid.uuid4())

def classify(score):
    if score is None: return None
    for label, thresh in SCORE_BANDS:
        if score >= thresh: return label
    return "FAIL"

def db():
    c=sqlite3.connect(DB)
    c.row_factory=sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c

def _migrate(c):
    """Additive columns introduced after some deployments already had a
    populated server.db. CREATE TABLE IF NOT EXISTS (in schema.sql) never
    alters an already-existing table, so add these explicitly here -- each
    ALTER TABLE is idempotent (a "duplicate column name" error just means an
    earlier run already applied it, safe to ignore)."""
    for stmt in (
        "ALTER TABLE session_target ADD COLUMN start_image_path TEXT",
        "ALTER TABLE shot ADD COLUMN corrected INTEGER NOT NULL DEFAULT 0",
    ):
        try:
            c.execute(stmt)
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e):
                raise

def init():
    with db() as c:
        c.executescript(open(SCHEMA,encoding="utf-8").read())
        _migrate(c)
        c.commit()

async def notify(event="update"):
    dead=[]
    for ws in clients:
        try: await ws.send_json({"type":event,"time":now()})
        except: dead.append(ws)
    for ws in dead: clients.discard(ws)

class MatchIn(BaseModel):
    code:str
    name:str
    description:str|None=None

class SessionIn(BaseModel):
    match_id:int
    lane_id:int
    shooter_id:int
    session_number:int|None=None

class SyncRequest(BaseModel):
    edge_uuid:str
    edge_code:str|None=None
    hostname:str|None=None
    ip_address:str|None=None
    software_version:str|None=None
    events:list[dict]=Field(default_factory=list)

class ManagerLoginIn(BaseModel):
    password:str

class ShotCorrectionIn(BaseModel):
    x_mm:float
    y_mm:float

class ResetConfirmIn(BaseModel):
    # A typed confirmation phrase, not just the manager token, since this
    # wipes EVERY row in the database irreversibly -- guards against firing
    # it by accident (a stray click on a saved request, a copy-pasted curl).
    confirm:str

@app.on_event("startup")
def startup():
    init()
    print(f"[server] READY build={BUILD_TAG} db={DB}")

@app.get("/api/health")
def health(): return {"ok":True,"time":now(),"build":BUILD_TAG,"db_path":DB}

def require_manager(x_manager_token:str|None=Header(default=None)):
    if not x_manager_token or x_manager_token not in manager_tokens:
        raise HTTPException(401,"manager login required")
    return True

@app.post("/api/manager/login")
def manager_login(x:ManagerLoginIn):
    if x.password != MANAGER_PASSWORD:
        raise HTTPException(401,"invalid password")
    token=secrets.token_hex(16)
    manager_tokens.add(token)
    return {"ok":True,"token":token}

@app.post("/api/manager/logout")
def manager_logout(x_manager_token:str|None=Header(default=None)):
    manager_tokens.discard(x_manager_token)
    return {"ok":True}

@app.get("/api/matches")
def matches():
    with db() as c:
        return [dict(x) for x in c.execute("""
          SELECT m.*,
                 COUNT(DISTINCT s.session_id) sessions,
                 COALESCE(SUM(s.total_shots),0) shots,
                 COALESCE(SUM(s.total_score),0) score
          FROM shooting_match m
          LEFT JOIN shooting_session s ON s.match_id=m.match_id
          GROUP BY m.match_id ORDER BY m.match_id DESC""")]

@app.post("/api/matches")
async def create_match(x:MatchIn):
    with db() as c:
        try:
            uid=U()
            cur=c.execute("""INSERT INTO shooting_match
                (uuid,match_code,match_name,status,description,start_time)
                VALUES(?,?,?,?,?,?)""",
                (uid,x.code,x.name,"PLANNED",x.description,now()))
            c.commit()
            result=dict(c.execute("SELECT * FROM shooting_match WHERE match_id=?",(cur.lastrowid,)).fetchone())
        except sqlite3.IntegrityError as e: raise HTTPException(409,str(e))
    await notify("match")
    return result

@app.post("/api/matches/{match_id}/start")
async def start_match(match_id:int):
    with db() as c:
        c.execute("UPDATE shooting_match SET status='RUNNING',start_time=? WHERE match_id=?",(now(),match_id))
        c.commit()
    await notify("match")
    return {"ok":True}

@app.post("/api/matches/{match_id}/complete")
async def complete_match(match_id:int):
    with db() as c:
        c.execute("UPDATE shooting_match SET status='COMPLETED',end_time=? WHERE match_id=?",(now(),match_id))
        c.execute("""UPDATE shooting_session SET status='COMPLETED',end_time=?
                     WHERE match_id=? AND status NOT IN ('COMPLETED','CANCELLED')""",(now(),match_id))
        c.commit()
    await notify("match")
    return {"ok":True}

@app.get("/api/matches/{match_id}")
def match_detail(match_id:int):
    with db() as c:
        m=c.execute("SELECT * FROM shooting_match WHERE match_id=?",(match_id,)).fetchone()
        if not m: raise HTTPException(404,"match not found")
        sessions=[dict(x) for x in c.execute("""
          SELECT s.*,l.lane_number,l.lane_name,sh.shooter_code,sh.name shooter_name
          FROM shooting_session s
          JOIN lane l ON l.lane_id=s.lane_id
          JOIN shooter sh ON sh.shooter_id=s.shooter_id
          WHERE s.match_id=? ORDER BY l.lane_number,s.session_number""",(match_id,))]
        return {"match":dict(m),"sessions":sessions}

@app.get("/api/units")
def units():
    with db() as c: return [dict(x) for x in c.execute("SELECT * FROM unit ORDER BY unit_name")]

@app.get("/api/shooters")
def shooters():
    with db() as c:
        return [dict(x) for x in c.execute("""
          SELECT s.*,u.unit_name FROM shooter s JOIN unit u ON u.unit_id=s.unit_id
          ORDER BY u.unit_name,s.name""")]

@app.get("/api/edges")
def edges():
    with db() as c: return [dict(x) for x in c.execute("SELECT * FROM edge_device ORDER BY edge_code")]

@app.get("/api/lanes")
def lanes():
    with db() as c:
        return [dict(x) for x in c.execute("""
          SELECT l.*,e.edge_code,
                 COUNT(pt.physical_target_id) target_count
          FROM lane l
          JOIN edge_device e ON e.edge_id=l.edge_id
          LEFT JOIN physical_target pt ON pt.lane_id=l.lane_id AND pt.status='ACTIVE'
          GROUP BY l.lane_id ORDER BY l.lane_number""")]

@app.get("/api/lanes/{lane_id}/targets")
def lane_targets(lane_id:int):
    with db() as c:
        return [dict(x) for x in c.execute("""
          SELECT * FROM physical_target
          WHERE lane_id=? ORDER BY target_position""",(lane_id,))]

@app.post("/api/sessions")
async def create_session(x:SessionIn):
    with db() as c:
        lane=c.execute("SELECT * FROM lane WHERE lane_id=?",(x.lane_id,)).fetchone()
        match=c.execute("SELECT * FROM shooting_match WHERE match_id=?",(x.match_id,)).fetchone()
        if not lane or not match: raise HTTPException(404,"lane/match not found")
        uid=U()
        cur=c.execute("""INSERT INTO shooting_session
          (uuid,match_id,lane_id,shooter_id,session_number,start_time,status)
          VALUES(?,?,?,?,?,?,?)""",
          (uid,x.match_id,x.lane_id,x.shooter_id,x.session_number,now(),"READY"))
        session_id=cur.lastrowid
        targets=c.execute("""SELECT * FROM physical_target
                            WHERE lane_id=? AND status='ACTIVE'
                            ORDER BY target_position""",(x.lane_id,)).fetchall()
        for t in targets:
            c.execute("""INSERT INTO session_target
              (uuid,session_id,physical_target_id,target_number,status)
              VALUES(?,?,?,?,?)""",
              (U(),session_id,t["physical_target_id"],t["target_position"],"WAITING"))
        c.commit()
        result=dict(c.execute("SELECT * FROM shooting_session WHERE session_id=?",(session_id,)).fetchone())
    await notify("session")
    return result

@app.post("/api/sessions/{session_id}/start")
async def start_session(session_id:int):
    with db() as c:
        c.execute("UPDATE shooting_session SET status='RUNNING',start_time=? WHERE session_id=?",(now(),session_id))
        c.execute("UPDATE session_target SET status='ACTIVE',start_time=? WHERE session_id=? AND status='WAITING'",(now(),session_id))
        c.commit()
    await notify("session")
    return {"ok":True}

# ---------------------------------------------------------------------------
# Edge sync: an edge device (edge.py) has its OWN sqlite db with its own
# autoincrement ids, so every cross-reference in a sync payload is a uuid.
# Handlers below resolve those uuids to this server's own local ids.
# ---------------------------------------------------------------------------

def _lookup_id(c, table, id_col, uuid_val):
    if not uuid_val: return None
    row=c.execute(f"SELECT {id_col} FROM {table} WHERE uuid=?", (uuid_val,)).fetchone()
    return row[0] if row else None

def _apply_status_update(c, table:str, p:dict):
    """Partial UPDATE for session/session_target status payloads: only
    touches columns actually present so a status-only event never clobbers
    other fields."""
    cols=[col for col in ("status","start_time","end_time") if p.get(col) is not None]
    if not cols or not p.get("uuid"):
        return
    vals=[p[col] for col in cols]
    vals.append(p["uuid"])
    c.execute(f"UPDATE {table} SET {','.join(f'{c0}=?' for c0 in cols)} WHERE uuid=?", vals)

def _save_image_b64(name:str, image_b64:str|None):
    """Decode a base64 PNG sent by an edge device and store it under /images
    as "<name>.png". Returns the URL path to record in the db, or None."""
    if not image_b64:
        return None
    try:
        raw=base64.b64decode(image_b64)
    except (binascii.Error, ValueError):
        return None
    fname=f"{name}.png"
    with open(os.path.join(IMAGES_DIR,fname),"wb") as f:
        f.write(raw)
    return f"/images/{fname}"

def _save_shot_image(shot_uuid:str, image_b64:str|None):
    return _save_image_b64(shot_uuid, image_b64)

def _sync_unit(c, edge_id, p):
    # Natural key is unit_code. If a unit with this code already exists under
    # a DIFFERENT uuid (e.g. the edge's local db was reset/recreated, or an
    # older server.db still had this code from a previous test run), RECLAIM
    # it: adopt the incoming uuid instead of silently no-op'ing, which is what
    # plain INSERT OR IGNORE did and is what caused edge/server uuids to
    # permanently diverge for the same logical entity.
    c.execute("""INSERT INTO unit(uuid,unit_code,unit_name,unit_type)
                 VALUES(?,?,?,?)
                 ON CONFLICT(unit_code) DO UPDATE SET
                   uuid=excluded.uuid, unit_name=excluded.unit_name, unit_type=excluded.unit_type""",
              (p["uuid"],p["unit_code"],p["unit_name"],p.get("unit_type")))
    return True

def _sync_shooter(c, edge_id, p):
    unit_id=_lookup_id(c,"unit","unit_id",p.get("unit_uuid"))
    if not unit_id:
        return False
    # Natural key is (unit_id, shooter_code); reclaim on conflict, same reasoning as unit.
    c.execute("""INSERT INTO shooter(uuid,unit_id,shooter_code,name)
                 VALUES(?,?,?,?)
                 ON CONFLICT(unit_id, shooter_code) DO UPDATE SET
                   uuid=excluded.uuid, name=excluded.name""",
              (p["uuid"],unit_id,p["shooter_code"],p["name"]))
    return True

def _sync_lane(c, edge_id, p):
    # Natural key is lane_number (globally unique). This is the entity that
    # broke silently: if server.db already had lane_number=N registered under
    # an old uuid, INSERT OR IGNORE would skip the row entirely, leaving the
    # edge's fresh lane uuid unresolvable server-side -- and every physical
    #_target/session/shot underneath that lane would then fail to find its
    # parent and vanish with no error. Reclaiming here fixes it at the root.
    c.execute("""INSERT INTO lane(uuid,edge_id,lane_number,lane_name)
                 VALUES(?,?,?,?)
                 ON CONFLICT(lane_number) DO UPDATE SET
                   uuid=excluded.uuid, edge_id=excluded.edge_id, lane_name=excluded.lane_name""",
              (p["uuid"],edge_id,p["lane_number"],p.get("lane_name")))
    return True

def _sync_physical_target(c, edge_id, p):
    lane_id=_lookup_id(c,"lane","lane_id",p.get("lane_uuid"))
    if not lane_id:
        return False
    # Natural key is (lane_id, target_position); reclaim on conflict.
    c.execute("""INSERT INTO physical_target
        (uuid,lane_id,target_code,target_position,target_width_mm,target_height_mm,
         image_width_px,image_height_px,calibration_data)
        VALUES(?,?,?,?,?,?,?,?,?)
        ON CONFLICT(lane_id, target_position) DO UPDATE SET
          uuid=excluded.uuid, target_code=excluded.target_code,
          target_width_mm=excluded.target_width_mm, target_height_mm=excluded.target_height_mm,
          image_width_px=excluded.image_width_px, image_height_px=excluded.image_height_px,
          calibration_data=excluded.calibration_data""",
        (p["uuid"],lane_id,p["target_code"],p["target_position"],p.get("target_width_mm"),
         p.get("target_height_mm"),p.get("image_width_px"),p.get("image_height_px"),
         p.get("calibration_data")))
    return True

def _sync_match(c, edge_id, p):
    c.execute("""INSERT OR IGNORE INTO shooting_match(uuid,match_code,match_name,status,start_time)
                 VALUES(?,?,?,?,?)""",
              (p["uuid"],p["match_code"],p["match_name"],p.get("status","RUNNING"),p.get("start_time")))
    return True

def _sync_session_create(c, edge_id, p):
    match_id=_lookup_id(c,"shooting_match","match_id",p.get("match_uuid"))
    lane_id=_lookup_id(c,"lane","lane_id",p.get("lane_uuid"))
    shooter_id=_lookup_id(c,"shooter","shooter_id",p.get("shooter_uuid"))
    if match_id and lane_id and shooter_id:
        c.execute("""INSERT OR IGNORE INTO shooting_session
            (uuid,match_id,lane_id,shooter_id,session_number,start_time,status)
            VALUES(?,?,?,?,?,?,?)""",
            (p["uuid"],match_id,lane_id,shooter_id,p.get("session_number"),
             p.get("start_time"),p.get("status","RUNNING")))
        return True
    return False

def _sync_session_target_create(c, edge_id, p):
    session_id=_lookup_id(c,"shooting_session","session_id",p.get("session_uuid"))
    pt_id=_lookup_id(c,"physical_target","physical_target_id",p.get("physical_target_uuid"))
    if session_id and pt_id:
        # "Start" image: the blank/empty target face rendered before any shot
        # is fired, sent once here so the viewer's compare panel can show it
        # next to the shooting/finished image (see /api/targets/{id}/detail).
        start_image_path=_save_image_b64(f"start_{p['uuid']}", p.get("start_image_b64"))
        c.execute("""INSERT OR IGNORE INTO session_target
            (uuid,session_id,physical_target_id,target_number,status,start_time,start_image_path)
            VALUES(?,?,?,?,?,?,?)""",
            (p["uuid"],session_id,pt_id,p["target_number"],p.get("status","ACTIVE"),p.get("start_time"),start_image_path))
        return True
    return False

def _sync_shot(c, edge_id, p):
    target_id=_lookup_id(c,"session_target","target_id",p.get("target_uuid"))
    if target_id is None:
        return False
    image_path=_save_shot_image(p.get("uuid"), p.get("image_b64")) or p.get("image_path")
    cur=c.execute("""INSERT OR IGNORE INTO shot
      (uuid,target_id,shot_number,shot_timestamp,x_pixel,y_pixel,x_mm,y_mm,
       distance_mm,score,confidence,detection_method,image_path,frame_id)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
      (p["uuid"],target_id,p["shot_number"],p["shot_timestamp"],
       p.get("x_pixel"),p.get("y_pixel"),p.get("x_mm"),p.get("y_mm"),
       p.get("distance_mm"),p.get("score"),p.get("confidence"),
       p.get("detection_method"),image_path,p.get("frame_id")))
    if cur.rowcount:
        c.execute("""UPDATE session_target
           SET total_score=total_score+?,total_shots=total_shots+1
           WHERE target_id=?""",(p.get("score",0),target_id))
        c.execute("""UPDATE shooting_session SET
           total_score=(SELECT COALESCE(SUM(total_score),0) FROM session_target WHERE session_id=shooting_session.session_id),
           total_shots=(SELECT COALESCE(SUM(total_shots),0) FROM session_target WHERE session_id=shooting_session.session_id)
           WHERE session_id=(SELECT session_id FROM session_target WHERE target_id=?)""",(target_id,))
        return True
    return False

@app.post("/api/sync")
async def sync(req:SyncRequest):
    accepted=0
    skipped=[]  # every event that did NOT apply, with why -- so a broken
                # sync is always visible in the response instead of silently
                # vanishing (this is what let edge/server uuids diverge before).
    with db() as c:
        edge=c.execute("SELECT * FROM edge_device WHERE uuid=?",(req.edge_uuid,)).fetchone()
        if not edge:
            # No row for this exact uuid yet. edge_code is also UNIQUE though:
            # if this edge's local db was ever reset/recreated it shows up
            # with a brand-new edge_uuid but the SAME edge_code as before
            # (e.g. "EDGE-LANE1"). A plain INSERT would then hit the
            # edge_code UNIQUE constraint and raise -- uncaught, since this
            # runs before the per-event try/except -- turning into an HTTP
            # 500 that fails the WHOLE sync request (topology + shots
            # included). Upsert-by-edge_code instead: reclaim the row.
            edge_code = req.edge_code or f"EDGE-{req.edge_uuid[:8]}"
            c.execute("""INSERT INTO edge_device
                (uuid,edge_code,hostname,ip_address,software_version,status,last_seen)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(edge_code) DO UPDATE SET
                  uuid=excluded.uuid, hostname=excluded.hostname, ip_address=excluded.ip_address,
                  software_version=excluded.software_version, status='ONLINE', last_seen=excluded.last_seen""",
                (req.edge_uuid, edge_code, req.hostname, req.ip_address, req.software_version, "ONLINE", now()))
            edge_id=c.execute("SELECT edge_id FROM edge_device WHERE edge_code=?",(edge_code,)).fetchone()[0]
        else:
            edge_id=edge["edge_id"]
            c.execute("UPDATE edge_device SET status='ONLINE',last_seen=? WHERE uuid=?",(now(),req.edge_uuid))

        for ev in req.events:
            p=ev.get("payload",{}) or {}
            typ=ev.get("entity_type")
            etype=ev.get("event_type","UPDATE")
            ok=False
            try:
                if typ=="unit": ok=_sync_unit(c,edge_id,p)
                elif typ=="shooter": ok=_sync_shooter(c,edge_id,p)
                elif typ=="lane": ok=_sync_lane(c,edge_id,p)
                elif typ=="physical_target": ok=_sync_physical_target(c,edge_id,p)
                elif typ=="shooting_match": ok=_sync_match(c,edge_id,p)
                elif typ=="session":
                    if etype=="CREATE": ok=_sync_session_create(c,edge_id,p)
                    else: _apply_status_update(c,"shooting_session",p); ok=True
                elif typ=="session_target":
                    if etype=="CREATE": ok=_sync_session_target_create(c,edge_id,p)
                    else: _apply_status_update(c,"session_target",p); ok=True
                elif typ=="shot":
                    ok=_sync_shot(c,edge_id,p)
                else:
                    skipped.append({"entity_type":typ,"event_type":etype,"reason":"unknown entity_type"})
                    continue
            except Exception as e:
                print(f"{typ}/{etype} sync error:",e)
                skipped.append({"entity_type":typ,"event_type":etype,"reason":str(e)})
                continue
            if ok: accepted+=1
            else: skipped.append({"entity_type":typ,"event_type":etype,"reason":"parent entity not resolved yet (uuid not found)"})
        c.commit()
    if skipped:
        print(f"[server] sync from {req.edge_code}: {accepted} accepted, {len(skipped)} skipped:", skipped)
    await notify("sync")
    return {"ok":True,"accepted":accepted,"skipped":skipped}

@app.get("/api/lanes/{lane_id}/sessions")
def lane_sessions(lane_id:int):
    """History list for one lane: every session ever recorded on it (any
    match, any edge run), most recent first. Used by the viewer's history
    picker -- pair with GET /api/dashboard?session_id=... to load one of
    these in full (targets + images). Also returns shooters_on_lane: the
    count of DISTINCT shooters who have actually fired a shot on this lane,
    across its whole history -- not just the sessions listed above -- using
    the same "total_shots>0" definition as /api/dashboard's shooters_total
    and /api/stats/overview's shooters_participated, so the three numbers
    never disagree with each other."""
    with db() as c:
        rows=c.execute("""
          SELECT s.session_id, s.uuid, s.status, s.total_score, s.total_shots,
                 s.start_time, s.end_time, m.match_code, sh.name shooter_name, u.unit_name
          FROM shooting_session s
          JOIN shooting_match m ON m.match_id=s.match_id
          JOIN shooter sh ON sh.shooter_id=s.shooter_id
          JOIN unit u ON u.unit_id=sh.unit_id
          WHERE s.lane_id=?
          ORDER BY s.session_id DESC
        """,(lane_id,)).fetchall()
        shooters_on_lane=c.execute("""
          SELECT COUNT(DISTINCT shooter_id) n FROM shooting_session
          WHERE lane_id=? AND total_shots>0""",(lane_id,)).fetchone()["n"]
        return {"sessions":[dict(x) for x in rows], "shooters_on_lane":shooters_on_lane}

@app.get("/api/dashboard")
def dashboard(match_id:int|None=None, lane_id:int|None=None, session_id:int|None=None):
    """Live view of every lane. With no filters this returns, for EACH lane,
    its most recently created session (regardless of which match/edge it
    belongs to) -- that's "the current run on this lane" as far as the
    viewer is concerned, since edges create their own match per run and the
    viewer no longer manages matches at all. Pass session_id to fetch one
    specific (possibly historical) session in full instead -- used by the
    viewer's "view history" picker. match_id/lane_id narrow the live view
    (mainly for debugging)."""
    with db() as c:
        where=[]
        args=[]
        if session_id is not None:
            where.append("s.session_id=?"); args.append(session_id)
        else:
            if match_id is not None:
                where.append("s.match_id=?"); args.append(match_id)
            if lane_id is not None:
                where.append("s.lane_id=?"); args.append(lane_id)
            if not where:
                # One row per lane: the session with the highest session_id
                # (i.e. most recently created) for that lane.
                where.append("s.session_id=(SELECT MAX(session_id) FROM shooting_session WHERE lane_id=s.lane_id)")
        q=f"""
        SELECT s.session_id,s.uuid,s.match_id,s.lane_id,s.shooter_id,s.status,
               s.total_score,s.total_shots,s.start_time,s.end_time,
               l.lane_number,l.lane_name,
               sh.shooter_code,sh.name shooter_name,
               u.unit_id,u.unit_name,
               m.match_code
        FROM shooting_session s
        JOIN lane l ON l.lane_id=s.lane_id
        JOIN shooter sh ON sh.shooter_id=s.shooter_id
        JOIN unit u ON u.unit_id=sh.unit_id
        JOIN shooting_match m ON m.match_id=s.match_id
        WHERE {' AND '.join(where)}
        ORDER BY l.lane_number
        """
        sessions=[dict(x) for x in c.execute(q,args)]
        for s in sessions:
            s["targets"]=[dict(x) for x in c.execute("""
              SELECT st.target_id,st.target_number,st.status,st.total_score,st.total_shots,
                     pt.target_code,st.start_image_path,
                     (SELECT sh2.image_path FROM shot sh2 WHERE sh2.target_id=st.target_id
                        ORDER BY sh2.shot_id DESC LIMIT 1) AS last_image,
                     (SELECT sh2.score FROM shot sh2 WHERE sh2.target_id=st.target_id
                        ORDER BY sh2.shot_id DESC LIMIT 1) AS last_score
              FROM session_target st JOIN physical_target pt ON pt.physical_target_id=st.physical_target_id
              WHERE st.session_id=? ORDER BY st.target_number""",(s["session_id"],))]

        session_ids=[s["session_id"] for s in sessions]
        recent=[]
        if session_ids:
            ph=",".join(["?"]*len(session_ids))
            recent=[dict(x) for x in c.execute(f"""
              SELECT sh.shot_timestamp,sh.score,sh.distance_mm,sh.x_mm,sh.y_mm,sh.image_path,
                     st.target_number,l.lane_number,ss.shooter_id,sho.name shooter_name
              FROM shot sh
              JOIN session_target st ON st.target_id=sh.target_id
              JOIN shooting_session ss ON ss.session_id=st.session_id
              JOIN lane l ON l.lane_id=ss.lane_id
              JOIN shooter sho ON sho.shooter_id=ss.shooter_id
              WHERE ss.session_id IN ({ph})
              ORDER BY sh.shot_id DESC LIMIT 200""", session_ids)]

        # Distinct shooters who have actually fired at least one shot, ACROSS
        # THE ENTIRE RANGE HISTORY (every lane, every match, every session
        # ever) -- not just the sessions/lanes currently shown above. Uses
        # the exact same "total_shots>0" definition as lane_sessions()'s
        # shooters_on_lane and stats_overview()'s shooters_participated, so
        # the viewer's two counters and the manager statistics panel always
        # agree with each other.
        shooters_total=c.execute("""
          SELECT COUNT(DISTINCT shooter_id) n FROM shooting_session
          WHERE total_shots>0""").fetchone()["n"]
        return {"sessions":sessions,"recent_shots":recent,"shooters_total":shooters_total}

# ---------------------------------------------------------------------------
# Shot recheck / correction: a shooter can compare the "start" (blank) image
# against the shooting/finished image for one target and spot a detection
# mistake; a manager can then click a corrected impact point on the image and
# have the shot's score AND position updated (not just the score), so the
# stored record matches what actually happened downhill. Shooters in one
# unit may have fired on different lanes/sessions -- this endpoint operates
# purely on target_id / shot_id (session_target/shot primary keys), so it
# works the same regardless of which lane or session produced the shot.
# ---------------------------------------------------------------------------

@app.get("/api/targets/{target_id}/detail")
def target_detail(target_id:int):
    """Everything the viewer's before/after compare panel needs for one
    session_target: the blank "start" image plus every shot fired at it (each
    already has its own cumulative image_path from when it was fired, so the
    LAST shot's image_path is the "current"/"finished" image to compare
    against start_image_path)."""
    with db() as c:
        st=c.execute("SELECT * FROM session_target WHERE target_id=?",(target_id,)).fetchone()
        if not st: raise HTTPException(404,"target not found")
        shots=[dict(x) for x in c.execute("""
          SELECT shot_id,shot_number,shot_timestamp,x_mm,y_mm,distance_mm,score,image_path,corrected
          FROM shot WHERE target_id=? ORDER BY shot_number""",(target_id,))]
        return {"target":dict(st),"shots":shots}

@app.post("/api/shots/{shot_id}/correct")
async def correct_shot(shot_id:int, x:ShotCorrectionIn, _:bool=Depends(require_manager)):
    """Manager-only: recompute a shot's score from a corrected impact
    position (x_mm,y_mm), using the exact same formula edge.py/edge_qt use
    when the shot is first fired, then recompute the affected target's and
    session's total_score. Does NOT regenerate the stored shot image (that
    would need cascading regeneration of every LATER cumulative image for the
    same target) -- only the numeric record changes; the viewer marks the
    shot as "corrected" instead. Logged to event_log for audit."""
    with db() as c:
        shot=c.execute("SELECT * FROM shot WHERE shot_id=?",(shot_id,)).fetchone()
        if not shot: raise HTTPException(404,"shot not found")
        dist=(x.x_mm**2 + x.y_mm**2) ** 0.5
        score=max(0, round(10.0 - dist/25.0, 1))
        x_pixel=450 + x.x_mm/0.5
        y_pixel=450 - x.y_mm/0.5
        c.execute("""UPDATE shot SET x_mm=?,y_mm=?,distance_mm=?,score=?,x_pixel=?,y_pixel=?,corrected=1
                     WHERE shot_id=?""",
                  (round(x.x_mm,2),round(x.y_mm,2),round(dist,2),score,x_pixel,y_pixel,shot_id))
        target_id=shot["target_id"]
        c.execute("""UPDATE session_target SET
             total_score=(SELECT COALESCE(SUM(score),0) FROM shot WHERE target_id=?)
             WHERE target_id=?""",(target_id,target_id))
        session_id=c.execute("SELECT session_id FROM session_target WHERE target_id=?",(target_id,)).fetchone()[0]
        c.execute("""UPDATE shooting_session SET
             total_score=(SELECT COALESCE(SUM(total_score),0) FROM session_target WHERE session_id=?)
             WHERE session_id=?""",(session_id,session_id))
        c.execute("""INSERT INTO event_log(uuid,event_type,entity_type,entity_id,entity_uuid,payload,severity)
                     VALUES(?,?,?,?,?,?,?)""",
                  (U(),"CORRECTION","shot",shot_id,shot["uuid"],
                   json.dumps({"old":{"x_mm":shot["x_mm"],"y_mm":shot["y_mm"],"score":shot["score"]},
                               "new":{"x_mm":x.x_mm,"y_mm":x.y_mm,"score":score}}),
                   "INFO"))
        c.commit()
        result=dict(c.execute("SELECT * FROM shot WHERE shot_id=?",(shot_id,)).fetchone())
    await notify("correction")
    return result

# ---------------------------------------------------------------------------
# Manager statistics: score-band classification (Excellent/Good/OK/Fail),
# broken down overall, per lane+target, and per unit, plus general totals.
# Every count below is at the SHOT level (each individual shot is classified
# by its own score) since that's the finest-grained, least ambiguous unit --
# per-lane/per-unit numbers are then just a GROUP BY away from the same
# classification. All endpoints require a manager login (see
# /api/manager/login) via the X-Manager-Token header. SQLite (3.25+, bundled
# with any modern Python) supports the same FILTER(WHERE ...) aggregate
# syntax used here as PostgreSQL, so this needed no translation on revert.
# ---------------------------------------------------------------------------

_CLASS_COUNTS_SQL = """
    COUNT(sh.shot_id) shots,
    COUNT(*) FILTER (WHERE sh.score>=9) excellent,
    COUNT(*) FILTER (WHERE sh.score>=7 AND sh.score<9) good,
    COUNT(*) FILTER (WHERE sh.score>=5 AND sh.score<7) ok,
    COUNT(*) FILTER (WHERE sh.score<5) fail
"""

@app.get("/api/stats/overview")
def stats_overview(_:bool=Depends(require_manager)):
    with db() as c:
        by_result=dict(c.execute(f"""
          SELECT {_CLASS_COUNTS_SQL} FROM shot sh""").fetchone())

        by_lane=[dict(x) for x in c.execute(f"""
          SELECT l.lane_id,l.lane_number,l.lane_name,{_CLASS_COUNTS_SQL}
          FROM lane l
          LEFT JOIN shooting_session ses ON ses.lane_id=l.lane_id
          LEFT JOIN session_target st ON st.session_id=ses.session_id
          LEFT JOIN shot sh ON sh.target_id=st.target_id
          GROUP BY l.lane_id,l.lane_number,l.lane_name
          ORDER BY l.lane_number""")]

        by_lane_target=[dict(x) for x in c.execute(f"""
          SELECT l.lane_number,pt.target_position,{_CLASS_COUNTS_SQL}
          FROM physical_target pt
          JOIN lane l ON l.lane_id=pt.lane_id
          LEFT JOIN session_target st ON st.physical_target_id=pt.physical_target_id
          LEFT JOIN shot sh ON sh.target_id=st.target_id
          GROUP BY l.lane_number,pt.target_position
          ORDER BY l.lane_number,pt.target_position""")]

        by_unit=[dict(x) for x in c.execute(f"""
          SELECT u.unit_id,u.unit_name,
                 COUNT(DISTINCT s.shooter_id) shooters,
                 COUNT(DISTINCT ses.session_id) sessions,
                 {_CLASS_COUNTS_SQL}
          FROM unit u
          LEFT JOIN shooter s ON s.unit_id=u.unit_id
          LEFT JOIN shooting_session ses ON ses.shooter_id=s.shooter_id
          LEFT JOIN session_target st ON st.session_id=ses.session_id
          LEFT JOIN shot sh ON sh.target_id=st.target_id
          GROUP BY u.unit_id,u.unit_name
          ORDER BY u.unit_name""")]

        common=dict(c.execute("""
          SELECT
            (SELECT COUNT(*) FROM shot) total_shots,
            (SELECT COUNT(*) FROM shooting_session) total_sessions,
            (SELECT COUNT(*) FROM shooting_session WHERE status='COMPLETED') completed_sessions,
            (SELECT COUNT(DISTINCT shooter_id) FROM shooting_session WHERE total_shots>0) shooters_participated,
            (SELECT COUNT(*) FROM shooter) total_shooters,
            (SELECT COUNT(*) FROM unit) total_units,
            (SELECT COUNT(*) FROM lane) total_lanes,
            (SELECT COUNT(*) FROM edge_device) total_edges
        """).fetchone())

        return {"by_result":by_result,"by_lane":by_lane,"by_lane_target":by_lane_target,
                "by_unit":by_unit,"common":common,
                "bands":{"excellent_min":9,"good_min":7,"ok_min":5}}

# ---------------------------------------------------------------------------
# Testing helper: wipe every row from every table (plus rendered images) so
# you can start a completely clean run without stopping the server or
# deleting server.db by hand. Manager-authenticated AND requires a typed
# confirmation phrase in the body -- this is IRREVERSIBLE and NOT meant to be
# wired into any normal UI button, only used deliberately while testing
# (curl, the /docs Swagger page, or a throwaway script).
# ---------------------------------------------------------------------------

@app.post("/api/admin/reset")
async def admin_reset(body:ResetConfirmIn, _:bool=Depends(require_manager)):
    """Deletes every row from every table in server.db (schema/tables
    themselves are kept, just emptied) and deletes every file under
    images/. Foreign keys are temporarily switched off for the wipe so table
    order doesn't matter (self-referencing `unit.parent_unit_id` in
    particular would otherwise reject a plain per-table DELETE), then
    switched back on before the connection closes. Autoincrement counters
    (sqlite_sequence) are reset too, so IDs start again from 1."""
    if body.confirm != "DELETE ALL":
        raise HTTPException(400,"confirm must be exactly the text 'DELETE ALL'")
    with db() as c:
        c.execute("PRAGMA foreign_keys=OFF")
        tables=[r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]
        deleted_rows={}
        for tbl in tables:
            deleted_rows[tbl]=c.execute(f"DELETE FROM {tbl}").rowcount
        try:
            c.execute("DELETE FROM sqlite_sequence")
        except sqlite3.OperationalError:
            pass  # no autoincrement columns used yet -- fine
        c.execute("PRAGMA foreign_keys=ON")
        c.commit()
    deleted_images=0
    for fname in os.listdir(IMAGES_DIR):
        fpath=os.path.join(IMAGES_DIR,fname)
        try:
            if os.path.isfile(fpath):
                os.remove(fpath); deleted_images+=1
        except OSError:
            pass
    await notify("reset")
    return {"ok":True,"deleted_rows":deleted_rows,"deleted_images":deleted_images}

@app.websocket("/ws")
async def websocket(ws:WebSocket):
    await ws.accept(); clients.add(ws)
    try:
        while True: await ws.receive_text()
    except WebSocketDisconnect: clients.discard(ws)

# Reviewed camera rounds have their own geometry and evidence, not simulator formulas.
from real_api import install as install_real_api
install_real_api(app, require_manager, DB, IMAGES_DIR)
