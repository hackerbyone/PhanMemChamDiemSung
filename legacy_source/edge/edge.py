"""Edge simulator: Camera -> Detection -> Score -> SQLite (edge.db) -> SyncQueue -> Server.

Each edge.py process simulates ONE lane (with M physical targets, default 2).
Run two instances in parallel -- one per lane -- to simulate a two-lane range:

    python edge.py --server http://SERVER:8000 --lane 1 --targets 2 --shots 10
    python edge.py --server http://SERVER:8000 --lane 2 --targets 2 --shots 10

To evaluate several PEOPLE taking turns on ONE lane instead (one full
session each, back to back), use --people N instead of running multiple
lanes:

    python edge.py --server http://SERVER:8000 --lane 1 --people 4 --targets 2 --shots 10

Each instance is its own independent "edge device": its own local sqlite db
(edge_lane<N>.db by default, so two instances never collide on disk), its own
edge_uuid, its own unit/shooter/match, all scoped to the lane number it was
given. Because `lane.lane_number` is globally unique server-side, running two
instances with the SAME --lane would collide by design (that's the schema
saying "one lane number = one physical lane"), so always give parallel
instances different --lane values.

For every shot it renders a small visualization image of the target face with
all hits recorded so far, stores everything locally, and pushes it all
(topology + shots + status) to the server over /api/sync so a web viewer can
show live per-lane status ("shooting" / "done" / ...) and the target image,
updating in realtime no matter which edge produced the shot.
"""
import os, sqlite3, uuid, json, random, time, argparse, base64, socket, threading, csv, shutil
from datetime import datetime, timezone
import requests
from PIL import Image, ImageDraw

BASE = os.path.dirname(os.path.abspath(__file__))
SCHEMA = os.path.join(os.path.dirname(BASE), "schema.sql")

# Must match the calibration_data seeded onto physical_target below
# (scale_mm_per_px=0.5, 900x900px image -> ~450x450mm target -> +/-225mm half-width)
TARGET_MM_RANGE = 225
IMG_PX = 320


def U(): return str(uuid.uuid4())
def now(): return datetime.now(timezone.utc).isoformat()

# Default Vietnamese shooter names, cycled by lane number so each parallel
# edge gets a distinct, realistic name out of the box. Override any single
# lane's name with --shooter-name "Ho Ten" on the command line.
VN_SHOOTER_NAMES = [
    "Nguyễn Văn A", "Trần Xuân B", "Lê Thị C", "Phạm Văn D", "Hoàng Thị E",
    "Đỗ Văn F", "Vũ Thị G", "Bùi Văn H", "Ngô Văn I", "Dương Thị K",
]
def default_shooter_name(lane): return VN_SHOOTER_NAMES[(lane - 1) % len(VN_SHOOTER_NAMES)]


class Edge:
    """Bundles the paths/state for one edge instance so two of them can run
    in the same process (used by tests) or, normally, in two separate OS
    processes each with their own instance."""

    def __init__(self, lane, db_path=None, images_dir=None, edge_code=None):
        self.lane = lane
        self.db_path = db_path or os.path.join(BASE, f"edge{lane}.db")
        self.images_dir = images_dir or os.path.join(BASE, f"images_lane{lane}")
        self.edge_code = edge_code or f"EDGE-LANE{lane}"
        os.makedirs(self.images_dir, exist_ok=True)

    def db(self):
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        return c

    def init(self):
        with self.db() as c:
            c.executescript(open(SCHEMA, encoding="utf8").read())
            # Additive columns introduced after some edge<N>.db files already
            # existed on disk; CREATE TABLE IF NOT EXISTS (in schema.sql)
            # never alters an already-existing table, so add these here too
            # (idempotent -- "duplicate column name" just means an earlier
            # run of this same edge already applied it).
            for stmt in (
                "ALTER TABLE session_target ADD COLUMN start_image_path TEXT",
                "ALTER TABLE shot ADD COLUMN corrected INTEGER NOT NULL DEFAULT 0",
            ):
                try:
                    c.execute(stmt)
                except sqlite3.OperationalError as e:
                    if "duplicate column name" not in str(e):
                        raise
            c.commit()


def render_target_image(path, hits, size=IMG_PX, mm_range=TARGET_MM_RANGE):
    """Draw a simple target face (scoring rings) plus every hit recorded so far
    for that target, most-recent hit included. This simulates the
    camera+detection visualization a real edge device would produce."""
    scale = (size / 2 - 12) / mm_range
    cx = cy = size / 2
    img = Image.new("RGB", (size, size), "white")
    d = ImageDraw.Draw(img)
    rings = 10
    for i in range(rings, 0, -1):
        r_px = (mm_range * i / rings) * scale
        shade = 235 - int(i * 10)
        d.ellipse([cx - r_px, cy - r_px, cx + r_px, cy + r_px], outline=(shade, shade, shade))
    d.line([cx - 7, cy, cx + 7, cy], fill=(0, 0, 0))
    d.line([cx, cy - 7, cx, cy + 7], fill=(0, 0, 0))
    for x, y, score in hits:
        px, py = cx + x * scale, cy - y * scale
        r = 5
        color = (0, 140, 0) if score >= 8 else (230, 140, 0) if score >= 5 else (200, 0, 0)
        d.ellipse([px - r, py - r, px + r, py + r], fill=color, outline=(0, 0, 0))
    img.save(path, "PNG")
    return path


def queue_event(c, edge_id, event_type, entity_type, entity_id, entity_uuid, payload):
    """Append an outbound sync event. event_type is CREATE (full record, used
    the first time an entity is seeded) or UPDATE (partial, status changes)."""
    c.execute("""INSERT INTO sync_queue(uuid,edge_id,event_type,entity_type,entity_id,entity_uuid,payload)
                 VALUES(?,?,?,?,?,?,?)""",
              (U(), edge_id, event_type, entity_type, entity_id, entity_uuid, json.dumps(payload)))


def get_or_create_edge_row(c, edge_code):
    row = c.execute("SELECT * FROM edge_device WHERE edge_code=?", (edge_code,)).fetchone()
    if row:
        return row["uuid"], row["edge_id"]
    edge_uuid = U()
    cur = c.execute("""INSERT INTO edge_device
        (uuid,edge_code,hostname,ip_address,software_version,config_version,status)
        VALUES(?,?,?,?,?,?,?)""",
        (edge_uuid, edge_code, socket.gethostname(), "127.0.0.1", "1.0.0", 1, "ONLINE"))
    return edge_uuid, cur.lastrowid


def seed(edge: Edge, targets=2, shooter_name=None, shooter_code=None, unit_code=None, unit_name=None):
    """Create (idempotently) this edge's local unit/shooter/lane/target/match
    topology -- all scoped to THIS edge's lane number, so nothing it creates
    can collide with another edge simulating a different lane -- and queue
    CREATE sync events for all of it, so the server can build the same
    picture even though it starts from an empty database.

    Each of unit/shooter/lane/physical_target is an UPSERT keyed on its
    natural key (unit_code / (unit_id,shooter_code) / lane_number /
    (lane_id,target_position)) that only refreshes the mutable fields
    (name, etc) and never touches uuid on conflict. That keeps THIS edge's
    local identity stable across restarts (same edge_lane<N>.db => same
    uuids => idempotent sync), while still letting you change e.g. the
    shooter's name on an existing local db and have it actually take effect
    and propagate to the server on the next run, instead of the name being
    stuck forever behind the old INSERT OR IGNORE.

    `shooter_code` defaults to "S<lane>" (one shooter identity per lane, as
    before) but can be overridden -- e.g. "S01-02" -- so that MULTIPLE
    distinct people can each get their own persistent shooter record on the
    SAME lane, used by run_people_sequence() below to evaluate N people back
    to back without them all colliding onto a single shared shooter row.

    `unit_code`/`unit_name` default to a value DERIVED FROM THE LANE
    ("UNIT-LANE<N>" / "Lane <N> Unit"), same as before, but can be overridden
    -- shooters that belong to one real-world unit are not necessarily all
    assigned to the same physical lane, so a session's unit must be settable
    independently of which lane happens to run it (used by --unit-code/
    --unit-name and by the --roster/--people sequences below, where each
    entry can carry its own unit instead of inheriting the lane's default)."""
    edge.init()
    shooter_name = shooter_name or default_shooter_name(edge.lane)
    shooter_code = shooter_code or f"S{edge.lane:02d}"
    unit_code = unit_code or f"UNIT-LANE{edge.lane}"
    unit_name = unit_name or f"Lane {edge.lane} Unit"
    with edge.db() as c:
        edge_uuid, edge_id = get_or_create_edge_row(c, edge.edge_code)

        c.execute("""INSERT INTO unit(uuid,unit_code,unit_name,unit_type) VALUES(?,?,?,?)
                     ON CONFLICT(unit_code) DO UPDATE SET
                       unit_name=excluded.unit_name, unit_type=excluded.unit_type""",
                  (U(), unit_code, unit_name, "DEMO"))
        unit = c.execute("SELECT * FROM unit WHERE unit_code=?", (unit_code,)).fetchone()
        queue_event(c, edge_id, "CREATE", "unit", unit["unit_id"], unit["uuid"],
                    {"uuid": unit["uuid"], "unit_code": unit["unit_code"],
                     "unit_name": unit["unit_name"], "unit_type": unit["unit_type"]})

        c.execute("""INSERT INTO shooter(uuid,unit_id,shooter_code,name) VALUES(?,?,?,?)
                     ON CONFLICT(unit_id, shooter_code) DO UPDATE SET name=excluded.name""",
                  (U(), unit["unit_id"], shooter_code, shooter_name))
        shooter = c.execute("SELECT * FROM shooter WHERE unit_id=? AND shooter_code=?",
                             (unit["unit_id"], shooter_code)).fetchone()
        queue_event(c, edge_id, "CREATE", "shooter", shooter["shooter_id"], shooter["uuid"],
                    {"uuid": shooter["uuid"], "unit_uuid": unit["uuid"],
                     "shooter_code": shooter["shooter_code"], "name": shooter["name"]})

        c.execute("""INSERT INTO lane(uuid,edge_id,lane_number,lane_name) VALUES(?,?,?,?)
                     ON CONFLICT(lane_number) DO UPDATE SET lane_name=excluded.lane_name""",
                  (U(), edge_id, edge.lane, f"Lane {edge.lane}"))
        lane = c.execute("SELECT * FROM lane WHERE lane_number=?", (edge.lane,)).fetchone()
        queue_event(c, edge_id, "CREATE", "lane", lane["lane_id"], lane["uuid"],
                    {"uuid": lane["uuid"], "edge_uuid": edge_uuid,
                     "lane_number": lane["lane_number"], "lane_name": lane["lane_name"]})

        for p in range(1, targets + 1):
            code = f"L{edge.lane}-T{p}"
            calib = json.dumps({"scale_mm_per_px": 0.5, "cx": 450, "cy": 450})
            c.execute("""INSERT INTO physical_target
                (uuid,lane_id,target_code,target_position,target_width_mm,target_height_mm,
                 image_width_px,image_height_px,calibration_data)
                VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(lane_id, target_position) DO UPDATE SET
                  target_code=excluded.target_code, target_width_mm=excluded.target_width_mm,
                  target_height_mm=excluded.target_height_mm, image_width_px=excluded.image_width_px,
                  image_height_px=excluded.image_height_px, calibration_data=excluded.calibration_data""",
                (U(), lane["lane_id"], code, p, 450, 450, 900, 900, calib))
            pt = c.execute("""SELECT * FROM physical_target
                              WHERE lane_id=? AND target_position=?""", (lane["lane_id"], p)).fetchone()
            queue_event(c, edge_id, "CREATE", "physical_target", pt["physical_target_id"], pt["uuid"],
                        {"uuid": pt["uuid"], "lane_uuid": lane["uuid"], "target_code": pt["target_code"],
                         "target_position": pt["target_position"], "target_width_mm": pt["target_width_mm"],
                         "target_height_mm": pt["target_height_mm"], "image_width_px": pt["image_width_px"],
                         "image_height_px": pt["image_height_px"], "calibration_data": pt["calibration_data"]})

        match_uuid = U()
        match_code = f"MATCH-L{edge.lane}-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        c.execute("""INSERT INTO shooting_match(uuid,match_code,match_name,status,start_time)
                     VALUES(?,?,?,?,?)""", (match_uuid, match_code, f"Lane {edge.lane} Run", "RUNNING", now()))
        match = c.execute("SELECT * FROM shooting_match WHERE match_code=?", (match_code,)).fetchone()
        queue_event(c, edge_id, "CREATE", "shooting_match", match["match_id"], match["uuid"],
                    {"uuid": match["uuid"], "match_code": match["match_code"], "match_name": match["match_name"],
                     "status": match["status"], "start_time": match["start_time"]})
        c.commit()
    return edge_uuid, edge_id, match["match_id"], lane, shooter


def _start_session(server, lane, targets, shots, db_path=None, edge_code=None, shooter_name=None,
                    shooter_code=None, unit_code=None, unit_name=None):
    """Seed this lane's topology, open a new shooting_session + one
    session_target per physical target, and push all of that (topology +
    session start) to the server -- everything that has to happen ONCE,
    before any shot is fired. Returns a plain dict with everything the
    per-target firing functions below need."""
    edge = Edge(lane, db_path=db_path, edge_code=edge_code)
    edge_uuid, edge_id, match_id, lane_row, shooter = seed(edge, targets, shooter_name=shooter_name,
                                                            shooter_code=shooter_code, unit_code=unit_code,
                                                            unit_name=unit_name)
    print(f"[edge] Lane {lane} | EDGE UUID: {edge_uuid} | Shooter: {shooter['name']} | db={edge.db_path}")

    with edge.db() as c:
        match_uuid = c.execute("SELECT uuid FROM shooting_match WHERE match_id=?", (match_id,)).fetchone()["uuid"]

        sess_uuid = U()
        cur = c.execute("""INSERT INTO shooting_session
            (uuid,match_id,lane_id,shooter_id,session_number,start_time,status)
            VALUES(?,?,?,?,?,?,?)""",
            (sess_uuid, match_id, lane_row["lane_id"], shooter["shooter_id"], 1, now(), "RUNNING"))
        session_id = cur.lastrowid
        queue_event(c, edge_id, "CREATE", "session", session_id, sess_uuid,
                    {"uuid": sess_uuid, "match_uuid": match_uuid, "lane_uuid": lane_row["uuid"],
                     "shooter_uuid": shooter["uuid"], "session_number": 1,
                     "start_time": now(), "status": "RUNNING"})

        # LIMIT to exactly `targets` rows (by position): physical_target rows
        # from an EARLIER run of this same edge<N>.db with a DIFFERENT
        # --targets count are never deleted (seed() only creates/updates
        # positions 1..targets, it doesn't remove leftover higher positions),
        # so without this limit a lane run previously with --targets 4 and
        # now with --targets 3 would still pick up all 4 old physical_target
        # rows here and silently give this session 4 targets instead of 3.
        target_rows = c.execute("""SELECT * FROM physical_target
            WHERE lane_id=? ORDER BY target_position LIMIT ?""", (lane_row["lane_id"], targets)).fetchall()
        for t in target_rows:
            st_uuid = U()
            # "Start" image: the blank target face, rendered with zero hits,
            # BEFORE this target's first shot -- sent once here so the
            # viewer's compare panel can show it next to the shooting/
            # finished image (see server.py's session_target CREATE handler
            # and GET /api/targets/{id}/detail).
            start_img_name = f"{st_uuid}_start.png"
            start_img_path = os.path.join(edge.images_dir, start_img_name)
            render_target_image(start_img_path, hits=[])
            rel_start_path = f"images_lane{lane}/{start_img_name}"
            cur2 = c.execute("""INSERT INTO session_target
                (uuid,session_id,physical_target_id,target_number,status,start_time,start_image_path)
                VALUES(?,?,?,?,?,?,?)""",
                (st_uuid, session_id, t["physical_target_id"], t["target_position"], "ACTIVE", now(), rel_start_path))
            with open(start_img_path, "rb") as f:
                start_b64 = base64.b64encode(f.read()).decode("ascii")
            queue_event(c, edge_id, "CREATE", "session_target", cur2.lastrowid, st_uuid,
                        {"uuid": st_uuid, "session_uuid": sess_uuid, "physical_target_uuid": t["uuid"],
                         "target_number": t["target_position"], "status": "ACTIVE", "start_time": now(),
                         "start_image_b64": start_b64})
        c.commit()

    # Push topology + "shooting started" status before any shots exist.
    sync(edge, server, edge_uuid)

    with edge.db() as c:
        target_ids = [t["target_number"] for t in c.execute(
            """SELECT st.target_number FROM session_target st WHERE st.session_id=?
               ORDER BY st.target_number""", (session_id,)).fetchall()]

    return {"edge": edge, "lane": lane, "edge_uuid": edge_uuid, "edge_id": edge_id,
            "session_id": session_id, "sess_uuid": sess_uuid, "target_ids": target_ids}


def _fire_target_block(state, server, target_position, shots, interval):
    """Fire this ONE target's entire string of shots (all `shots` of them),
    syncing each shot to the server immediately as it happens. Used both by
    the single-lane sequential run() and, called from its own thread per
    lane, by the multi-lane synchronized-by-target-wave orchestrator."""
    edge, lane, edge_id, edge_uuid = state["edge"], state["lane"], state["edge_id"], state["edge_uuid"]
    session_id = state["session_id"]
    for n in range(1, shots + 1):
        with edge.db() as c:
            st = c.execute("""SELECT st.*, pt.target_code FROM session_target st
                JOIN physical_target pt ON pt.physical_target_id=st.physical_target_id
                WHERE st.session_id=? AND st.target_number=?""",
                (session_id, target_position)).fetchone()

            x = random.uniform(-180, 180); y = random.uniform(-180, 180)
            dist = (x * x + y * y) ** 0.5
            score = max(0, round(10.0 - dist / 25.0, 1))

            prior = c.execute("""SELECT x_mm,y_mm,score FROM shot
                                  WHERE target_id=? ORDER BY shot_number""", (st["target_id"],)).fetchall()
            hits = [(h["x_mm"], h["y_mm"], h["score"]) for h in prior] + [(x, y, score)]
            img_name = f"{st['uuid']}_{n:02d}.png"
            img_path = os.path.join(edge.images_dir, img_name)
            render_target_image(img_path, hits)

            p = {
                "uuid": U(), "target_id": st["target_id"], "target_uuid": st["uuid"], "shot_number": n,
                "shot_timestamp": now(), "x_pixel": 450 + x / 0.5, "y_pixel": 450 - y / 0.5,
                "x_mm": round(x, 2), "y_mm": round(y, 2), "distance_mm": round(dist, 2),
                "score": score, "confidence": round(random.uniform(.96, .999), 3),
                "detection_method": "SIM_MOBILE_NET", "frame_id": f"SIM-{time.time_ns()}",
                "image_path": f"images_lane{lane}/{img_name}",
            }
            c.execute("""INSERT INTO shot
                (uuid,target_id,shot_number,shot_timestamp,x_pixel,y_pixel,x_mm,y_mm,
                 distance_mm,score,confidence,detection_method,image_path,frame_id)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (p["uuid"], p["target_id"], p["shot_number"], p["shot_timestamp"],
                 p["x_pixel"], p["y_pixel"], p["x_mm"], p["y_mm"], p["distance_mm"],
                 p["score"], p["confidence"], p["detection_method"], p["image_path"], p["frame_id"]))
            c.execute("""UPDATE session_target SET total_score=total_score+?,total_shots=total_shots+1
                         WHERE target_id=?""", (score, st["target_id"]))
            c.execute("""UPDATE shooting_session SET
                total_score=(SELECT COALESCE(SUM(total_score),0) FROM session_target WHERE session_id=shooting_session.session_id),
                total_shots=(SELECT COALESCE(SUM(total_shots),0) FROM session_target WHERE session_id=shooting_session.session_id)
                WHERE session_id=?""", (st["session_id"],))

            with open(img_path, "rb") as f:
                p["image_b64"] = base64.b64encode(f.read()).decode("ascii")
            queue_event(c, edge_id, "CREATE", "shot", None, p["uuid"], p)

            if n == shots:
                c.execute("""UPDATE session_target SET status='COMPLETED',end_time=?
                             WHERE target_id=?""", (now(), st["target_id"]))
                queue_event(c, edge_id, "UPDATE", "session_target", st["target_id"], st["uuid"],
                            {"uuid": st["uuid"], "status": "COMPLETED", "end_time": now()})
            c.commit()
        sync(edge, server, edge_uuid)  # push THIS shot now, before the next shot fires
        print(f"[edge] Lane {lane} | Target {target_position}: shot {n}/{shots}")
        if n < shots:
            time.sleep(interval)


def _complete_session(state, server):
    edge, edge_uuid = state["edge"], state["edge_uuid"]
    with edge.db() as c:
        c.execute("""UPDATE shooting_session SET status='COMPLETED',end_time=?
                     WHERE session_id=?""", (now(), state["session_id"]))
        queue_event(c, state["edge_id"], "UPDATE", "session", state["session_id"], state["sess_uuid"],
                    {"uuid": state["sess_uuid"], "status": "COMPLETED", "end_time": now()})
        c.commit()
    sync(edge, server, edge_uuid)
    print(f"[edge] Lane {state['lane']} | Simulation complete.")


def run(server="http://127.0.0.1:8000", lane=1, targets=2, shots=10, interval=0.5,
        db_path=None, edge_code=None, shooter_name=None, shooter_code=None,
        unit_code=None, unit_name=None, target_interval=1.0):
    """Single lane, full simulation, targets fired one at a time in FULL
    BLOCKS: target 1 gets its entire string (all `shots` shots) before target
    2 starts at all, then target 2's full string before target 3, etc. --
    not round-robin. --interval paces consecutive shots on the SAME target;
    --target-interval is the pause after a target's string completes, before
    the next target starts."""
    state = _start_session(server, lane, targets, shots, db_path=db_path, edge_code=edge_code,
                            shooter_name=shooter_name, shooter_code=shooter_code,
                            unit_code=unit_code, unit_name=unit_name)
    target_ids = state["target_ids"]
    for ti, target_position in enumerate(target_ids):
        _fire_target_block(state, server, target_position, shots, interval)
        if ti < len(target_ids) - 1:
            print(f"[edge] Lane {lane} | Target {target_position} complete -- moving to target {target_ids[ti+1]}")
            time.sleep(target_interval)
    _complete_session(state, server)


def run_sequential(server="http://127.0.0.1:8000", lanes=(1, 2), targets=2, shots=10,
                    interval=0.5, target_interval=1.0, shooter_names=None,
                    unit_codes=None, unit_names=None):
    """Run each lane's FULL simulation one after another, in this single
    process: lane 1 seeds, fires all its shots, and is marked COMPLETED
    before lane 2 even starts seeding. Each lane still gets its own separate
    local database (edge<N>.db, e.g. edge1.db / edge2.db) and its own
    edge_uuid/unit/shooter/match, exactly as if it were a separate physical
    device. `shooter_names`/`unit_codes`/`unit_names` are optional dicts
    {lane_number: value} for per-lane overrides; lanes without an entry fall
    back to the usual lane-derived defaults. NOTE: this is the OLD
    lane-by-lane ordering -- for lanes that fire in sync (all lanes' target 1
    together, then all target 2, etc.), use run_parallel_by_target() / CLI
    `--lanes` (the default) instead.
    """
    shooter_names = shooter_names or {}
    unit_codes = unit_codes or {}
    unit_names = unit_names or {}
    for lane in lanes:
        print(f"[edge] ==== Starting lane {lane} (previous lane(s) fully complete) ====")
        run(server=server, lane=lane, targets=targets, shots=shots, interval=interval,
            target_interval=target_interval, shooter_name=shooter_names.get(lane),
            unit_code=unit_codes.get(lane), unit_name=unit_names.get(lane))


def run_parallel_by_target(server="http://127.0.0.1:8000", lanes=(1, 2), targets=2, shots=10,
                            interval=0.5, target_interval=1.0, shooter_names=None,
                            unit_codes=None, unit_names=None):
    """Run every lane's shooting in lockstep, synchronized by TARGET, not by
    lane: all lanes fire their target-1 string AT THE SAME TIME (each lane in
    its own thread), then -- once every lane has fully finished target 1 --
    all lanes fire target 2 at the same time, then target 3, etc. This is
    NOT lane1-then-lane2-then-lane3 (that is run_sequential()); it is
    target1(all lanes)-then-target2(all lanes)-then-target3(all lanes).
    Each lane still gets its own separate database (edge<N>.db) and identity.
    `shooter_names`/`unit_codes`/`unit_names` are optional dicts
    {lane_number: value}.
    """
    shooter_names = shooter_names or {}
    unit_codes = unit_codes or {}
    unit_names = unit_names or {}

    # 1. Seed every lane's topology + open its session first (order between
    #    lanes here doesn't matter -- no shots fired yet -- so just do it
    #    one at a time; it's fast).
    states = {}
    for lane in lanes:
        states[lane] = _start_session(server, lane, targets, shots, shooter_name=shooter_names.get(lane),
                                       unit_code=unit_codes.get(lane), unit_name=unit_names.get(lane))

    # 2. Fire target 1 on EVERY lane concurrently (one thread per lane), wait
    #    for ALL of them to finish their full target-1 string, THEN move on
    #    to target 2 for every lane, etc. Each lane has its own sqlite db
    #    file so the threads never touch the same connection/file.
    target_waves = states[lanes[0]]["target_ids"]  # same --targets count for every lane
    for wi, target_position in enumerate(target_waves):
        print(f"[edge] ==== Wave: target {target_position} on ALL lanes ({list(lanes)}) starts now ====")
        threads = [threading.Thread(target=_fire_target_block,
                                     args=(states[lane], server, target_position, shots, interval),
                                     name=f"lane{lane}-target{target_position}")
                   for lane in lanes]
        for th in threads: th.start()
        for th in threads: th.join()
        print(f"[edge] ==== Wave: target {target_position} complete on ALL lanes ====")
        if wi < len(target_waves) - 1:
            time.sleep(target_interval)

    # 3. All targets done on all lanes -> mark every lane's session COMPLETED.
    for lane in lanes:
        _complete_session(states[lane], server)
    print(f"[edge] All lanes complete (synchronized by target): {list(lanes)}.")
    print(f"[edge] All lanes complete, in order: {list(lanes)}.")


def run_people_sequence(server="http://127.0.0.1:8000", lane=1, targets=2, shots=10, interval=0.5,
                         target_interval=1.0, n_people=4, people_names=None, person_interval=2.0,
                         db_path=None, edge_code=None, unit_code=None, unit_name=None):
    """Evaluate N shooters back-to-back on ONE lane/edge: person 1 fires a
    full session (every target, full blocks) and is marked COMPLETED, THEN
    person 2 steps up to the SAME lane and fires their own full session,
    etc, until all n_people have been scored. This is the "sequence to
    evaluate the shoot score of N people" mode -- e.g. --people 4 or
    --people 5 -- distinct from --lanes (multiple physical lanes at once):
    here it's multiple PEOPLE taking turns on one lane.

    Each person gets their OWN persistent shooter record (shooter_code
    "S<lane>-<person index>", e.g. S01-01, S01-02, ...) so their individual
    results stay separately identifiable afterwards in the viewer's history
    dropdown and in the server's manager statistics (by-unit shooter counts,
    etc), even though every person shares the same lane/edge device and
    unit. `people_names` is an optional list of up to n_people names; any
    remaining slots fall back to the default Vietnamese name pool."""
    names = list(people_names or [])
    while len(names) < n_people:
        names.append(VN_SHOOTER_NAMES[len(names) % len(VN_SHOOTER_NAMES)])

    for i in range(1, n_people + 1):
        person_name = names[i - 1]
        shooter_code = f"S{lane:02d}-{i:02d}"
        print(f"[edge] ==== Lane {lane} | Person {i}/{n_people}: {person_name} ({shooter_code}) steps up ====")
        state = _start_session(server, lane, targets, shots, db_path=db_path, edge_code=edge_code,
                                shooter_name=person_name, shooter_code=shooter_code,
                                unit_code=unit_code, unit_name=unit_name)
        target_ids = state["target_ids"]
        for ti, target_position in enumerate(target_ids):
            _fire_target_block(state, server, target_position, shots, interval)
            if ti < len(target_ids) - 1:
                time.sleep(target_interval)
        _complete_session(state, server)
        print(f"[edge] Lane {lane} | Person {i}/{n_people} ({person_name}) done -- "
              f"total {state['session_id']} | score so far this session complete.")
        if i < n_people:
            time.sleep(person_interval)
    print(f"[edge] Lane {lane} | Evaluation sequence complete: {n_people} people scored.")


def load_roster(path):
    """Load a roster of {name, unit_code, unit_name, shooter_code} entries
    from a CSV or JSON file (format picked by extension: .json => JSON list
    of objects, anything else => CSV with a header row). Only `name` is
    really required -- unit_code/unit_name/shooter_code fall back to the
    same lane-derived defaults seed() itself uses when left blank, exactly
    like --people's shooter_code default -- but a roster's whole POINT is
    usually to give each entry its own unit, since shooters that belong to
    one real unit may be scheduled to shoot on different lanes."""
    ext = os.path.splitext(path)[1].lower()
    entries = []
    if ext == ".json":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for e in data:
            entries.append({"name": e.get("name"), "unit_code": e.get("unit_code"),
                             "unit_name": e.get("unit_name"), "shooter_code": e.get("shooter_code")})
    else:
        with open(path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                entries.append({"name": (row.get("name") or "").strip() or None,
                                 "unit_code": (row.get("unit_code") or "").strip() or None,
                                 "unit_name": (row.get("unit_name") or "").strip() or None,
                                 "shooter_code": (row.get("shooter_code") or "").strip() or None})
    return entries


def run_roster_sequence(server="http://127.0.0.1:8000", lane=1, targets=2, shots=10, interval=0.5,
                         target_interval=1.0, roster=None, person_interval=2.0,
                         db_path=None, edge_code=None):
    """Evaluate a roster of named shooters back-to-back on ONE lane, like
    run_people_sequence() above, but each entry carries its OWN unit_code/
    unit_name (falling back to the lane-derived default only if left blank)
    instead of every person implicitly sharing the lane's default unit.
    This is the fix for "shooters in one unit may shoot at different lanes":
    a roster entry's unit always follows the SHOOTER, never the lane that
    happens to run them, so results attribute correctly to the real unit no
    matter which physical lane processes that entry."""
    roster = roster or []
    n = len(roster)
    for i, entry in enumerate(roster, start=1):
        name = entry.get("name") or default_shooter_name(lane)
        shooter_code = entry.get("shooter_code") or f"S{lane:02d}-{i:02d}"
        unit_code = entry.get("unit_code")  # None => seed()'s own "UNIT-LANE<N>" default
        unit_name = entry.get("unit_name")
        print(f"[edge] ==== Lane {lane} | Roster {i}/{n}: {name} ({shooter_code})"
              f"{' / ' + unit_name if unit_name else ''}{' (' + unit_code + ')' if unit_code else ''} steps up ====")
        state = _start_session(server, lane, targets, shots, db_path=db_path, edge_code=edge_code,
                                shooter_name=name, shooter_code=shooter_code,
                                unit_code=unit_code, unit_name=unit_name)
        target_ids = state["target_ids"]
        for ti, target_position in enumerate(target_ids):
            _fire_target_block(state, server, target_position, shots, interval)
            if ti < len(target_ids) - 1:
                time.sleep(target_interval)
        _complete_session(state, server)
        print(f"[edge] Lane {lane} | Roster {i}/{n} ({name}) done -- session {state['session_id']} complete.")
        if i < n:
            time.sleep(person_interval)
    print(f"[edge] Lane {lane} | Roster sequence complete: {n} shooters scored.")


def sync(edge: Edge, server, edge_uuid):
    with edge.db() as c:
        edge_row = c.execute("SELECT * FROM edge_device WHERE uuid=?", (edge_uuid,)).fetchone()
        rows = c.execute("SELECT * FROM sync_queue WHERE status='PENDING' ORDER BY sync_id").fetchall()
        events = [{"entity_type": r["entity_type"], "event_type": r["event_type"], "payload": json.loads(r["payload"])}
                  for r in rows]
        if not events:
            return
    body = {
        "edge_uuid": edge_uuid,
        "edge_code": edge_row["edge_code"], "hostname": edge_row["hostname"],
        "ip_address": edge_row["ip_address"], "software_version": edge_row["software_version"],
        "events": events,
    }
    try:
        r = requests.post(server + "/api/sync", json=body, timeout=5)
        if r.ok:
            with edge.db() as c:
                for row in rows:
                    c.execute("UPDATE sync_queue SET status='SENT',sent_at=? WHERE sync_id=?", (now(), row["sync_id"]))
                c.commit()
            print(f"[edge] Lane {edge.lane} | sync:", r.json())
        else:
            print(f"[edge] Lane {edge.lane} | sync failed:", r.status_code, r.text)
    except Exception as e:
        print(f"[edge] Lane {edge.lane} | LAN unavailable; keeping local queue:", e)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://127.0.0.1:8000")
    ap.add_argument("--lane", type=int, default=1, help="Lane number this edge instance simulates (must be unique across parallel edges). Ignored if --lanes is given.")
    ap.add_argument("--lanes", default=None,
                     help="Comma-separated lane numbers to run TOGETHER in this one process, e.g. '1,2,3,4'. By "
                          "default (see --sequential) all lanes fire in lockstep synchronized BY TARGET: every "
                          "lane's target 1 fires at the same time (one thread per lane), then once ALL lanes have "
                          "fully finished target 1, every lane's target 2 fires at the same time, then target 3, "
                          "etc -- NOT lane1-then-lane2-then-lane3. Each lane still gets its own separate database "
                          "(edge<N>.db) and identity, exactly like separate --lane processes.")
    ap.add_argument("--sequential", action="store_true",
                     help="With --lanes, run lane 1's ENTIRE simulation fully before lane 2 starts (old behavior), "
                          "instead of the default synchronized-by-target-wave mode.")
    ap.add_argument("--targets", type=int, default=2, help="Number of physical targets on this lane (e.g. 2, 3, 4)")
    ap.add_argument("--shots", type=int, default=10, help="Shots fired at EACH target, e.g. 5 or 10, before moving to the next target")
    ap.add_argument("--interval", type=float, default=.5,
                     help="Delay (seconds) between consecutive shots on the SAME target (default 0.5)")
    ap.add_argument("--db", default=None, help="Override local sqlite db path (default: edge<N>.db, e.g. edge1.db). Only used with --lane, not --lanes.")
    ap.add_argument("--edge-code", default=None, help="Override edge_code (default: EDGE-LANE<N>). Only used with --lane, not --lanes.")
    ap.add_argument("--shooter-name", default=None,
                     help="Shooter's name (default: a Vietnamese name picked by lane number, e.g. 'Nguyễn Văn A'). "
                          "With --lanes, applies only if a single lane is given; for multiple lanes each gets its own default name. "
                          "Ignored with --people/--roster (each entry has its own name).")
    ap.add_argument("--shooter-code", default=None,
                     help="Override shooter_code (default: 'S<lane:02d>'). Only used with plain --lane (single "
                          "shooter); --people/--roster generate their own per-entry codes.")
    ap.add_argument("--unit-code", default=None,
                     help="Override unit_code (default: 'UNIT-LANE<N>', derived from --lane). Lets a shooter's unit "
                          "be set independently of which lane they happen to shoot on -- shooters in one unit may be "
                          "assigned to different lanes. With --lanes, applies only if a single lane is given. "
                          "Ignored with --roster (each entry carries its own unit).")
    ap.add_argument("--unit-name", default=None,
                     help="Override unit_name (default: 'Lane <N> Unit'). Same scoping rules as --unit-code.")
    ap.add_argument("--target-interval", type=float, default=1.0,
                     help="Pause (seconds) after target 1's full string of shots is complete, before target 2 starts "
                          "(then target 2 -> target 3, etc). Simulates the shooter moving aim between targets. Default 1.0")
    ap.add_argument("--people", type=int, default=None,
                     help="Evaluate N shooters sequentially on this ONE lane (--lane), one full session each, one "
                          "after another -- e.g. --people 4 or --people 5. Each person gets their own persistent "
                          "shooter record so their results stay separately identifiable afterwards. Cannot combine "
                          "with --lanes.")
    ap.add_argument("--people-names", default=None,
                     help="Comma-separated names for --people, e.g. 'An,Binh,Chi,Dung'. Fewer names than --people "
                          "falls back to the default Vietnamese name pool for the remaining people.")
    ap.add_argument("--person-interval", type=float, default=2.0,
                     help="Pause (seconds) after one person's session completes, before the next person's session "
                          "starts, with --people/--roster. Default 2.0")
    ap.add_argument("--roster", default=None,
                     help="Path to a CSV or JSON roster file of {name, unit_code, unit_name, shooter_code} entries; "
                          "runs each in sequence on ONE lane (--lane), one full session each -- like --people, but "
                          "each entry can carry its OWN unit instead of sharing the lane's default one, since "
                          "shooters in one unit may be assigned to shoot on different lanes. CSV needs a header row "
                          "with those column names (only 'name' is required); JSON is a list of objects with the "
                          "same keys. Cannot combine with --lanes/--people.")
    ap.add_argument("--reset", action="store_true",
                     help="TESTING: delete this edge's local data for a clean run, then exit immediately without "
                          "simulating anything (--targets/--shots/--people/--roster/etc are ignored). Removes "
                          "edge<N>.db (or --db's path) and the images_lane<N>/ folder for --lane, or for EVERY lane "
                          "listed in --lanes. This only clears local edge state -- it does NOT touch the server's "
                          "data (see POST /api/admin/reset in server.py for that).")
    a = ap.parse_args()
    if a.reset:
        lanes_to_reset = [int(x.strip()) for x in a.lanes.split(",") if x.strip()] if a.lanes else [a.lane]
        single = len(lanes_to_reset) == 1
        for lane in lanes_to_reset:
            db_path = a.db if (a.db and single) else os.path.join(BASE, f"edge{lane}.db")
            images_dir = os.path.join(BASE, f"images_lane{lane}")
            removed = []
            if os.path.exists(db_path):
                os.remove(db_path); removed.append(db_path)
            if os.path.isdir(images_dir):
                shutil.rmtree(images_dir); removed.append(images_dir)
            if removed:
                print(f"[edge] Lane {lane}: reset -- removed {', '.join(removed)}")
            else:
                print(f"[edge] Lane {lane}: nothing to reset (no local data found at {db_path})")
        raise SystemExit(0)
    if a.roster:
        if a.lanes or a.people:
            raise SystemExit("--roster cannot be combined with --lanes or --people (it evaluates a named list of "
                              "shooters, each possibly in a different unit, on ONE lane; use --lane to choose which).")
        roster = load_roster(a.roster)
        if not roster:
            raise SystemExit(f"--roster {a.roster!r} has no entries.")
        run_roster_sequence(a.server, a.lane, a.targets, a.shots, a.interval,
                             target_interval=a.target_interval, roster=roster,
                             person_interval=a.person_interval, db_path=a.db, edge_code=a.edge_code)
    elif a.people:
        if a.lanes:
            raise SystemExit("--people cannot be combined with --lanes (it evaluates multiple PEOPLE on ONE lane; "
                              "use --lane to choose which one).")
        names = [s.strip() for s in a.people_names.split(",") if s.strip()] if a.people_names else None
        run_people_sequence(a.server, a.lane, a.targets, a.shots, a.interval,
                             target_interval=a.target_interval, n_people=a.people, people_names=names,
                             person_interval=a.person_interval, db_path=a.db, edge_code=a.edge_code,
                             unit_code=a.unit_code, unit_name=a.unit_name)
    elif a.lanes:
        lane_list = [int(x.strip()) for x in a.lanes.split(",") if x.strip()]
        single = len(lane_list) == 1
        shooter_names = {lane_list[0]: a.shooter_name} if (a.shooter_name and single) else None
        unit_codes = {lane_list[0]: a.unit_code} if (a.unit_code and single) else None
        unit_names = {lane_list[0]: a.unit_name} if (a.unit_name and single) else None
        if a.sequential:
            run_sequential(a.server, lane_list, a.targets, a.shots, a.interval,
                            target_interval=a.target_interval, shooter_names=shooter_names,
                            unit_codes=unit_codes, unit_names=unit_names)
        else:
            run_parallel_by_target(a.server, lane_list, a.targets, a.shots, a.interval,
                                    target_interval=a.target_interval, shooter_names=shooter_names,
                                    unit_codes=unit_codes, unit_names=unit_names)
    else:
        run(a.server, a.lane, a.targets, a.shots, a.interval, db_path=a.db, edge_code=a.edge_code,
            shooter_name=a.shooter_name, shooter_code=a.shooter_code, target_interval=a.target_interval,
            unit_code=a.unit_code, unit_name=a.unit_name)
