-- PostgreSQL schema for the SERVER only (server.py). The edge simulator
-- (edge.py) keeps its own local SQLite db per lane (edge<N>.db) and its own
-- schema.sql -- only the central server's database moved to PostgreSQL, so
-- the two files intentionally diverge (SERIAL vs AUTOINCREMENT, no PRAGMA,
-- etc) even though the tables they describe are logically the same.

CREATE TABLE IF NOT EXISTS unit (
    unit_id SERIAL PRIMARY KEY,
    uuid TEXT NOT NULL UNIQUE,
    parent_unit_id INTEGER,
    unit_code TEXT NOT NULL UNIQUE,
    unit_name TEXT NOT NULL,
    unit_type TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at TEXT NOT NULL DEFAULT to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'),
    updated_at TEXT,
    FOREIGN KEY(parent_unit_id) REFERENCES unit(unit_id) ON DELETE RESTRICT ON UPDATE CASCADE,
    CHECK(status IN ('ACTIVE','INACTIVE'))
);

CREATE TABLE IF NOT EXISTS shooter (
    shooter_id SERIAL PRIMARY KEY,
    uuid TEXT NOT NULL UNIQUE,
    unit_id INTEGER NOT NULL,
    shooter_code TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at TEXT NOT NULL DEFAULT to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'),
    updated_at TEXT,
    FOREIGN KEY(unit_id) REFERENCES unit(unit_id) ON DELETE RESTRICT ON UPDATE CASCADE,
    UNIQUE(unit_id, shooter_code),
    CHECK(status IN ('ACTIVE','INACTIVE'))
);

CREATE TABLE IF NOT EXISTS edge_device (
    edge_id SERIAL PRIMARY KEY,
    uuid TEXT NOT NULL UNIQUE,
    edge_code TEXT NOT NULL UNIQUE,
    hostname TEXT,
    ip_address TEXT,
    software_version TEXT,
    config_version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'OFFLINE',
    last_seen TEXT,
    created_at TEXT NOT NULL DEFAULT to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'),
    updated_at TEXT,
    CHECK(status IN ('ONLINE','OFFLINE','ERROR','MAINTENANCE'))
);

CREATE TABLE IF NOT EXISTS lane (
    lane_id SERIAL PRIMARY KEY,
    uuid TEXT NOT NULL UNIQUE,
    edge_id INTEGER NOT NULL,
    lane_number INTEGER NOT NULL UNIQUE,
    lane_name TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    description TEXT,
    created_at TEXT NOT NULL DEFAULT to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'),
    updated_at TEXT,
    FOREIGN KEY(edge_id) REFERENCES edge_device(edge_id) ON DELETE RESTRICT,
    CHECK(lane_number > 0),
    CHECK(status IN ('ACTIVE','INACTIVE','MAINTENANCE','ERROR'))
);

CREATE TABLE IF NOT EXISTS physical_target (
    physical_target_id SERIAL PRIMARY KEY,
    uuid TEXT NOT NULL UNIQUE,
    lane_id INTEGER NOT NULL,
    target_code TEXT NOT NULL,
    target_position INTEGER NOT NULL,
    target_width_mm REAL,
    target_height_mm REAL,
    image_width_px INTEGER,
    image_height_px INTEGER,
    calibration_version INTEGER NOT NULL DEFAULT 1,
    calibration_data TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at TEXT NOT NULL DEFAULT to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'),
    updated_at TEXT,
    FOREIGN KEY(lane_id) REFERENCES lane(lane_id) ON DELETE RESTRICT,
    UNIQUE(lane_id,target_position),
    UNIQUE(lane_id,target_code),
    CHECK(target_position > 0),
    CHECK(status IN ('ACTIVE','INACTIVE','CALIBRATION','ERROR'))
);

CREATE TABLE IF NOT EXISTS shooting_match (
    match_id SERIAL PRIMARY KEY,
    uuid TEXT NOT NULL UNIQUE,
    match_code TEXT NOT NULL UNIQUE,
    match_name TEXT NOT NULL,
    start_time TEXT,
    end_time TEXT,
    status TEXT NOT NULL DEFAULT 'PLANNED',
    description TEXT,
    created_at TEXT NOT NULL DEFAULT to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'),
    updated_at TEXT,
    CHECK(status IN ('PLANNED','READY','RUNNING','PAUSED','COMPLETED','CANCELLED'))
);

CREATE TABLE IF NOT EXISTS shooting_session (
    session_id SERIAL PRIMARY KEY,
    uuid TEXT NOT NULL UNIQUE,
    match_id INTEGER NOT NULL,
    lane_id INTEGER NOT NULL,
    shooter_id INTEGER NOT NULL,
    session_number INTEGER,
    start_time TEXT,
    end_time TEXT,
    status TEXT NOT NULL DEFAULT 'WAITING',
    total_score REAL NOT NULL DEFAULT 0,
    total_shots INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'),
    updated_at TEXT,
    FOREIGN KEY(match_id) REFERENCES shooting_match(match_id) ON DELETE RESTRICT,
    FOREIGN KEY(lane_id) REFERENCES lane(lane_id) ON DELETE RESTRICT,
    FOREIGN KEY(shooter_id) REFERENCES shooter(shooter_id) ON DELETE RESTRICT,
    CHECK(total_shots >= 0),
    CHECK(status IN ('WAITING','READY','RUNNING','PAUSED','COMPLETED','CANCELLED','ERROR'))
);

CREATE TABLE IF NOT EXISTS session_target (
    target_id SERIAL PRIMARY KEY,
    uuid TEXT NOT NULL UNIQUE,
    session_id INTEGER NOT NULL,
    physical_target_id INTEGER NOT NULL,
    target_number INTEGER NOT NULL,
    start_time TEXT,
    end_time TEXT,
    status TEXT NOT NULL DEFAULT 'WAITING',
    total_score REAL NOT NULL DEFAULT 0,
    total_shots INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'),
    updated_at TEXT,
    FOREIGN KEY(session_id) REFERENCES shooting_session(session_id) ON DELETE CASCADE,
    FOREIGN KEY(physical_target_id) REFERENCES physical_target(physical_target_id) ON DELETE RESTRICT,
    UNIQUE(session_id,target_number),
    CHECK(target_number > 0),
    CHECK(total_shots >= 0),
    CHECK(status IN ('WAITING','ACTIVE','PAUSED','COMPLETED','CANCELLED','ERROR'))
);

CREATE TABLE IF NOT EXISTS shot (
    shot_id SERIAL PRIMARY KEY,
    uuid TEXT NOT NULL UNIQUE,
    target_id INTEGER NOT NULL,
    shot_number INTEGER NOT NULL,
    shot_timestamp TEXT NOT NULL,
    x_pixel REAL,
    y_pixel REAL,
    x_mm REAL,
    y_mm REAL,
    distance_mm REAL,
    score REAL,
    confidence REAL,
    detection_method TEXT,
    image_path TEXT,
    frame_id TEXT,
    created_at TEXT NOT NULL DEFAULT to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'),
    FOREIGN KEY(target_id) REFERENCES session_target(target_id) ON DELETE CASCADE,
    UNIQUE(target_id,shot_number),
    CHECK(shot_number > 0),
    CHECK(confidence IS NULL OR (confidence >= 0 AND confidence <= 1))
);

CREATE TABLE IF NOT EXISTS sync_queue (
    sync_id SERIAL PRIMARY KEY,
    uuid TEXT NOT NULL UNIQUE,
    edge_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id INTEGER,
    entity_uuid TEXT,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'),
    sent_at TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING',
    FOREIGN KEY(edge_id) REFERENCES edge_device(edge_id) ON DELETE RESTRICT,
    CHECK(status IN ('PENDING','SENDING','SENT','FAILED')),
    CHECK(retry_count >= 0)
);

CREATE TABLE IF NOT EXISTS event_log (
    event_id SERIAL PRIMARY KEY,
    uuid TEXT NOT NULL UNIQUE,
    edge_id INTEGER,
    event_type TEXT NOT NULL,
    entity_type TEXT,
    entity_id INTEGER,
    entity_uuid TEXT,
    event_time TEXT NOT NULL DEFAULT to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'),
    payload TEXT,
    severity TEXT NOT NULL DEFAULT 'INFO',
    FOREIGN KEY(edge_id) REFERENCES edge_device(edge_id) ON DELETE SET NULL,
    CHECK(severity IN ('DEBUG','INFO','WARNING','ERROR','CRITICAL'))
);

CREATE INDEX IF NOT EXISTS idx_shooter_unit ON shooter(unit_id);
CREATE INDEX IF NOT EXISTS idx_lane_edge ON lane(edge_id);
CREATE INDEX IF NOT EXISTS idx_pt_lane ON physical_target(lane_id);
CREATE INDEX IF NOT EXISTS idx_session_match ON shooting_session(match_id);
CREATE INDEX IF NOT EXISTS idx_session_lane ON shooting_session(lane_id);
CREATE INDEX IF NOT EXISTS idx_session_shooter ON shooting_session(shooter_id);
CREATE INDEX IF NOT EXISTS idx_st_session ON session_target(session_id);
CREATE INDEX IF NOT EXISTS idx_st_physical ON session_target(physical_target_id);
CREATE INDEX IF NOT EXISTS idx_shot_target ON shot(target_id);
CREATE INDEX IF NOT EXISTS idx_shot_time ON shot(shot_timestamp);
CREATE INDEX IF NOT EXISTS idx_sync_pending ON sync_queue(status,created_at);
CREATE INDEX IF NOT EXISTS idx_event_time ON event_log(event_time);
