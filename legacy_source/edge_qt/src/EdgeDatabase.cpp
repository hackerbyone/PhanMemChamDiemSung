#include "EdgeDatabase.h"
#include "NamePool.h"

#include <QCoreApplication>
#include <QDateTime>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QRandomGenerator>
#include <QSqlError>
#include <QSqlQuery>
#include <QSysInfo>
#include <QUuid>

#include <algorithm>
#include <chrono>
#include <cmath>

QString EdgeDatabase::nowIso()
{
    // Same idea as edge.py's now() (UTC, ISO-8601) though Qt renders it with
    // a trailing "Z" and millisecond precision instead of Python's
    // "+00:00" and microsecond precision. Every timestamp column here is
    // opaque TEXT to both this app and server.py -- nothing parses it back
    // into a date, it's only stored/displayed/sorted -- and both forms sort
    // correctly as plain strings, so the difference in formatting doesn't
    // matter functionally.
    return QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs);
}

QString EdgeDatabase::newUuid()
{
    return QUuid::createUuid().toString(QUuid::WithoutBraces);
}

QString EdgeDatabase::findSchemaPath()
{
    QStringList candidates;
    const QString dir = QCoreApplication::applicationDirPath();
    candidates << dir + QStringLiteral("/schema.sql");
    candidates << dir + QStringLiteral("/../schema.sql");
    candidates << dir + QStringLiteral("/../../schema.sql");
    candidates << dir + QStringLiteral("/../../../schema.sql");
    candidates << dir + QStringLiteral("/../../../../schema.sql");
#ifdef SCHEMA_SQL_SOURCE_PATH
    candidates << QStringLiteral(SCHEMA_SQL_SOURCE_PATH);
#endif
    for (const QString &c : candidates) {
        if (QFileInfo::exists(c))
            return c;
    }
    return QString();
}

bool EdgeDatabase::open(const QString &dbPath, const QString &connectionName)
{
    m_connectionName = connectionName;
    if (QSqlDatabase::contains(connectionName))
        m_db = QSqlDatabase::database(connectionName);
    else
        m_db = QSqlDatabase::addDatabase(QStringLiteral("QSQLITE"), connectionName);

    m_db.setDatabaseName(dbPath);
    if (!m_db.open()) {
        m_lastError = m_db.lastError().text();
        return false;
    }

    QSqlQuery pragma(m_db);
    pragma.exec(QStringLiteral("PRAGMA foreign_keys=ON"));

    // Load the schema straight off disk -- the exact same ../schema.sql
    // file edge.py and server.py use, so there is only ever one copy of
    // the schema to keep in sync (a Qt-resource-embedded copy was tried
    // first, but CMake's AUTORCC dependency scanner chokes on a .qrc entry
    // that points outside its own directory via "..", so plain disk I/O it
    // is). QSqlQuery::exec() only runs one statement at a time, unlike
    // Python's sqlite3 executescript(), so the script is split on ';' and
    // each statement is executed in turn -- safe here because this
    // particular schema file has no semicolons inside string/identifier
    // literals.
    const QString schemaPath = findSchemaPath();
    if (schemaPath.isEmpty()) {
        m_lastError = QStringLiteral(
            "could not locate schema.sql (looked next to the executable, "
            "1-4 folders above it, and at the project's build-time path)");
        return false;
    }
    QFile schemaFile(schemaPath);
    if (!schemaFile.open(QIODevice::ReadOnly | QIODevice::Text)) {
        m_lastError = QStringLiteral("could not open schema.sql at %1").arg(schemaPath);
        return false;
    }
    const QString schemaText = QString::fromUtf8(schemaFile.readAll());
    schemaFile.close();

    const QStringList statements = schemaText.split(QLatin1Char(';'), Qt::SkipEmptyParts);
    for (const QString &stmtRaw : statements) {
        const QString stmt = stmtRaw.trimmed();
        if (stmt.isEmpty())
            continue;
        QSqlQuery q(m_db);
        if (!q.exec(stmt)) {
            m_lastError = QStringLiteral("schema statement failed: %1 -- %2")
                              .arg(q.lastError().text(), stmt.left(60));
            return false;
        }
    }

    // Additive columns introduced after some edge<N>.db files already
    // existed on disk; "CREATE TABLE IF NOT EXISTS" (in schema.sql, above)
    // never alters an already-existing table, so add these explicitly here
    // too. Each ALTER TABLE is allowed to fail silently -- SQLite reports
    // "duplicate column name" when an earlier run of this same edge already
    // applied it, which is expected and harmless; any exec() result is
    // otherwise ignored the same way server.py's/edge.py's migration does.
    {
        QSqlQuery m1(m_db);
        m1.exec(QStringLiteral("ALTER TABLE session_target ADD COLUMN start_image_path TEXT"));
        QSqlQuery m2(m_db);
        m2.exec(QStringLiteral("ALTER TABLE shot ADD COLUMN corrected INTEGER NOT NULL DEFAULT 0"));
    }
    return true;
}

void EdgeDatabase::queueEvent(int edgeId, const QString &eventType, const QString &entityType, const QJsonObject &payload)
{
    // Only entity_type/event_type/payload are ever transmitted (see
    // fetchPendingEvents()); entity_id/entity_uuid below are purely local
    // bookkeeping, so entity_id is left NULL rather than plumbing through
    // every caller's local numeric id for no functional benefit.
    const QString entityUuid = payload.value(QStringLiteral("uuid")).toString();
    const QString payloadStr = QString::fromUtf8(QJsonDocument(payload).toJson(QJsonDocument::Compact));

    QSqlQuery q(m_db);
    q.prepare(QStringLiteral(
        "INSERT INTO sync_queue(uuid,edge_id,event_type,entity_type,entity_id,entity_uuid,payload) "
        "VALUES(?,?,?,?,?,?,?)"));
    q.addBindValue(newUuid());
    q.addBindValue(edgeId);
    q.addBindValue(eventType);
    q.addBindValue(entityType);
    q.addBindValue(QVariant()); // entity_id: NULL
    q.addBindValue(entityUuid);
    q.addBindValue(payloadStr);
    q.exec();
}

EdgeRow EdgeDatabase::getOrCreateEdgeRow(const QString &edgeCode)
{
    QSqlQuery sel(m_db);
    sel.prepare(QStringLiteral(
        "SELECT edge_id, uuid, edge_code, hostname, ip_address, software_version "
        "FROM edge_device WHERE edge_code=?"));
    sel.addBindValue(edgeCode);
    sel.exec();
    if (sel.next()) {
        EdgeRow e;
        e.id = sel.value(0).toInt();
        e.uuid = sel.value(1).toString();
        e.edgeCode = sel.value(2).toString();
        e.hostname = sel.value(3).toString();
        e.ipAddress = sel.value(4).toString();
        e.softwareVersion = sel.value(5).toString();
        return e;
    }

    const QString uuid = newUuid();
    const QString hostname = QSysInfo::machineHostName();
    const QString ip = QStringLiteral("127.0.0.1"); // matches edge.py's hardcoded ip_address
    const QString swVersion = QStringLiteral("1.0.0");

    QSqlQuery ins(m_db);
    ins.prepare(QStringLiteral(
        "INSERT INTO edge_device(uuid,edge_code,hostname,ip_address,software_version,config_version,status) "
        "VALUES(?,?,?,?,?,?,?)"));
    ins.addBindValue(uuid);
    ins.addBindValue(edgeCode);
    ins.addBindValue(hostname);
    ins.addBindValue(ip);
    ins.addBindValue(swVersion);
    ins.addBindValue(1);
    ins.addBindValue(QStringLiteral("ONLINE"));
    ins.exec();

    EdgeRow e;
    e.id = ins.lastInsertId().toInt();
    e.uuid = uuid;
    e.edgeCode = edgeCode;
    e.hostname = hostname;
    e.ipAddress = ip;
    e.softwareVersion = swVersion;
    return e;
}

UnitRow EdgeDatabase::upsertUnit(int edgeId, const QString &unitCode, const QString &unitName, const QString &unitType)
{
    QSqlQuery q(m_db);
    q.prepare(QStringLiteral(
        "INSERT INTO unit(uuid,unit_code,unit_name,unit_type) VALUES(?,?,?,?) "
        "ON CONFLICT(unit_code) DO UPDATE SET unit_name=excluded.unit_name, unit_type=excluded.unit_type"));
    q.addBindValue(newUuid());
    q.addBindValue(unitCode);
    q.addBindValue(unitName);
    q.addBindValue(unitType);
    q.exec();

    QSqlQuery sel(m_db);
    sel.prepare(QStringLiteral("SELECT unit_id, uuid, unit_code, unit_name FROM unit WHERE unit_code=?"));
    sel.addBindValue(unitCode);
    sel.exec();
    sel.next();

    UnitRow u;
    u.id = sel.value(0).toInt();
    u.uuid = sel.value(1).toString();
    u.code = sel.value(2).toString();
    u.name = sel.value(3).toString();

    const QJsonObject payload{
        {"uuid", u.uuid}, {"unit_code", u.code}, {"unit_name", u.name}, {"unit_type", unitType}};
    queueEvent(edgeId, QStringLiteral("CREATE"), QStringLiteral("unit"), payload);
    return u;
}

ShooterRow EdgeDatabase::upsertShooter(int edgeId, int unitId, const QString &unitUuid,
                                       const QString &shooterCode, const QString &shooterName)
{
    QSqlQuery q(m_db);
    q.prepare(QStringLiteral(
        "INSERT INTO shooter(uuid,unit_id,shooter_code,name) VALUES(?,?,?,?) "
        "ON CONFLICT(unit_id, shooter_code) DO UPDATE SET name=excluded.name"));
    q.addBindValue(newUuid());
    q.addBindValue(unitId);
    q.addBindValue(shooterCode);
    q.addBindValue(shooterName);
    q.exec();

    QSqlQuery sel(m_db);
    sel.prepare(QStringLiteral(
        "SELECT shooter_id, uuid, shooter_code, name FROM shooter WHERE unit_id=? AND shooter_code=?"));
    sel.addBindValue(unitId);
    sel.addBindValue(shooterCode);
    sel.exec();
    sel.next();

    ShooterRow s;
    s.id = sel.value(0).toInt();
    s.uuid = sel.value(1).toString();
    s.code = sel.value(2).toString();
    s.name = sel.value(3).toString();

    const QJsonObject payload{
        {"uuid", s.uuid}, {"unit_uuid", unitUuid}, {"shooter_code", s.code}, {"name", s.name}};
    queueEvent(edgeId, QStringLiteral("CREATE"), QStringLiteral("shooter"), payload);
    return s;
}

LaneRow EdgeDatabase::upsertLane(int edgeId, const QString &edgeUuid, int laneNumber, const QString &laneName)
{
    QSqlQuery q(m_db);
    q.prepare(QStringLiteral(
        "INSERT INTO lane(uuid,edge_id,lane_number,lane_name) VALUES(?,?,?,?) "
        "ON CONFLICT(lane_number) DO UPDATE SET lane_name=excluded.lane_name"));
    q.addBindValue(newUuid());
    q.addBindValue(edgeId);
    q.addBindValue(laneNumber);
    q.addBindValue(laneName);
    q.exec();

    QSqlQuery sel(m_db);
    sel.prepare(QStringLiteral("SELECT lane_id, uuid, lane_number, lane_name FROM lane WHERE lane_number=?"));
    sel.addBindValue(laneNumber);
    sel.exec();
    sel.next();

    LaneRow l;
    l.id = sel.value(0).toInt();
    l.uuid = sel.value(1).toString();
    l.number = sel.value(2).toInt();
    l.name = sel.value(3).toString();

    const QJsonObject payload{
        {"uuid", l.uuid}, {"edge_uuid", edgeUuid}, {"lane_number", l.number}, {"lane_name", l.name}};
    queueEvent(edgeId, QStringLiteral("CREATE"), QStringLiteral("lane"), payload);
    return l;
}

void EdgeDatabase::upsertPhysicalTargets(int edgeId, int laneId, const QString &laneUuid, int lane, int targets)
{
    for (int p = 1; p <= targets; ++p) {
        const QString code = QStringLiteral("L%1-T%2").arg(lane).arg(p);
        const QJsonObject calib{{"scale_mm_per_px", 0.5}, {"cx", 450}, {"cy", 450}};
        const QString calibStr = QString::fromUtf8(QJsonDocument(calib).toJson(QJsonDocument::Compact));

        QSqlQuery q(m_db);
        q.prepare(QStringLiteral(
            "INSERT INTO physical_target(uuid,lane_id,target_code,target_position,target_width_mm,"
            "target_height_mm,image_width_px,image_height_px,calibration_data) VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(lane_id, target_position) DO UPDATE SET "
            "target_code=excluded.target_code, target_width_mm=excluded.target_width_mm, "
            "target_height_mm=excluded.target_height_mm, image_width_px=excluded.image_width_px, "
            "image_height_px=excluded.image_height_px, calibration_data=excluded.calibration_data"));
        q.addBindValue(newUuid());
        q.addBindValue(laneId);
        q.addBindValue(code);
        q.addBindValue(p);
        q.addBindValue(450);
        q.addBindValue(450);
        q.addBindValue(900);
        q.addBindValue(900);
        q.addBindValue(calibStr);
        q.exec();

        QSqlQuery sel(m_db);
        sel.prepare(QStringLiteral(
            "SELECT uuid, target_code, target_position, target_width_mm, target_height_mm, "
            "image_width_px, image_height_px, calibration_data FROM physical_target "
            "WHERE lane_id=? AND target_position=?"));
        sel.addBindValue(laneId);
        sel.addBindValue(p);
        sel.exec();
        sel.next();

        const QJsonObject payload{
            {"uuid", sel.value(0).toString()},
            {"lane_uuid", laneUuid},
            {"target_code", sel.value(1).toString()},
            {"target_position", sel.value(2).toInt()},
            {"target_width_mm", sel.value(3).toDouble()},
            {"target_height_mm", sel.value(4).toDouble()},
            {"image_width_px", sel.value(5).toInt()},
            {"image_height_px", sel.value(6).toInt()},
            {"calibration_data", sel.value(7).toString()},
        };
        queueEvent(edgeId, QStringLiteral("CREATE"), QStringLiteral("physical_target"), payload);
    }
}

MatchRow EdgeDatabase::createMatch(int edgeId, int lane)
{
    const QString uuid = newUuid();
    // Pseudo-microsecond precision timestamp for uniqueness, since Qt's
    // date formatting only goes down to milliseconds (3 digits) versus
    // Python's strftime("%f") (6-digit microseconds): pad with 3 more
    // random digits so match_code collisions across sessions started in the
    // same millisecond remain effectively impossible.
    const QDateTime dt = QDateTime::currentDateTime();
    const QString ts = dt.toString(QStringLiteral("yyyyMMddHHmmss"))
        + QStringLiteral("%1").arg(dt.time().msec(), 3, 10, QChar('0'))
        + QStringLiteral("%1").arg(QRandomGenerator::global()->bounded(1000), 3, 10, QChar('0'));
    const QString code = QStringLiteral("MATCH-L%1-%2").arg(lane).arg(ts);
    const QString name = QStringLiteral("Lane %1 Run").arg(lane);
    const QString start = nowIso();

    QSqlQuery q(m_db);
    q.prepare(QStringLiteral(
        "INSERT INTO shooting_match(uuid,match_code,match_name,status,start_time) VALUES(?,?,?,?,?)"));
    q.addBindValue(uuid);
    q.addBindValue(code);
    q.addBindValue(name);
    q.addBindValue(QStringLiteral("RUNNING"));
    q.addBindValue(start);
    q.exec();

    MatchRow m;
    m.id = q.lastInsertId().toInt();
    m.uuid = uuid;
    m.code = code;
    m.name = name;

    const QJsonObject payload{
        {"uuid", m.uuid}, {"match_code", m.code}, {"match_name", m.name},
        {"status", "RUNNING"}, {"start_time", start}};
    queueEvent(edgeId, QStringLiteral("CREATE"), QStringLiteral("shooting_match"), payload);
    return m;
}

SeedResult EdgeDatabase::seedTopology(const QString &edgeCode, int lane, int targets,
                                      const QString &shooterName, const QString &shooterCode,
                                      const QString &unitCode, const QString &unitName)
{
    SeedResult r;
    r.edge = getOrCreateEdgeRow(edgeCode);

    const QString sName = shooterName.isEmpty() ? defaultShooterName(lane) : shooterName;
    const QString sCode = shooterCode.isEmpty()
        ? QStringLiteral("S%1").arg(lane, 2, 10, QChar('0'))
        : shooterCode;
    // unitCode/unitName default to a value DERIVED FROM THE LANE, same as
    // before, but a caller (single --unit-code/--unit-name override, or a
    // roster entry) can set them independently of the lane -- shooters that
    // belong to one real-world unit are not necessarily all on the same
    // physical lane.
    const QString uCode = unitCode.isEmpty() ? QStringLiteral("UNIT-LANE%1").arg(lane) : unitCode;
    const QString uName = unitName.isEmpty() ? QStringLiteral("Lane %1 Unit").arg(lane) : unitName;

    r.unit = upsertUnit(r.edge.id, uCode, uName, QStringLiteral("DEMO"));
    r.shooter = upsertShooter(r.edge.id, r.unit.id, r.unit.uuid, sCode, sName);
    r.lane = upsertLane(r.edge.id, r.edge.uuid, lane, QStringLiteral("Lane %1").arg(lane));
    upsertPhysicalTargets(r.edge.id, r.lane.id, r.lane.uuid, lane, targets);
    r.match = createMatch(r.edge.id, lane);
    return r;
}

SessionState EdgeDatabase::startSession(const SeedResult &seed, int lane, int targets, const QString &imagesDir)
{
    SessionState state;
    const QString sessUuid = newUuid();
    const QString start = nowIso();

    QSqlQuery q(m_db);
    q.prepare(QStringLiteral(
        "INSERT INTO shooting_session(uuid,match_id,lane_id,shooter_id,session_number,start_time,status) "
        "VALUES(?,?,?,?,?,?,?)"));
    q.addBindValue(sessUuid);
    q.addBindValue(seed.match.id);
    q.addBindValue(seed.lane.id);
    q.addBindValue(seed.shooter.id);
    q.addBindValue(1);
    q.addBindValue(start);
    q.addBindValue(QStringLiteral("RUNNING"));
    q.exec();

    const int sessionId = q.lastInsertId().toInt();
    state.sessionId = sessionId;
    state.sessionUuid = sessUuid;

    const QJsonObject sessPayload{
        {"uuid", sessUuid}, {"match_uuid", seed.match.uuid}, {"lane_uuid", seed.lane.uuid},
        {"shooter_uuid", seed.shooter.uuid}, {"session_number", 1}, {"start_time", start}, {"status", "RUNNING"}};
    queueEvent(seed.edge.id, QStringLiteral("CREATE"), QStringLiteral("session"), sessPayload);

    // Only the first `targets` physical_target rows by position -- leftover
    // rows from an earlier run of this same edge<N>.db with a HIGHER
    // --targets value must not leak into this session (see edge.py's
    // history entry #10).
    QSqlQuery trq(m_db);
    trq.prepare(QStringLiteral(
        "SELECT physical_target_id, uuid, target_position FROM physical_target "
        "WHERE lane_id=? ORDER BY target_position LIMIT ?"));
    trq.addBindValue(seed.lane.id);
    trq.addBindValue(targets);
    trq.exec();

    while (trq.next()) {
        const int ptId = trq.value(0).toInt();
        const QString ptUuid = trq.value(1).toString();
        const int position = trq.value(2).toInt();

        const QString stUuid = newUuid();
        const QString stStart = nowIso();

        // "Start" image: the blank target face, rendered with zero hits,
        // BEFORE this target's first shot -- sent once here so the viewer's
        // compare panel can show it next to the shooting/finished image.
        const QString startImgName = stUuid + QStringLiteral("_start.png");
        const QString startImgPath = imagesDir + QStringLiteral("/") + startImgName;
        const QByteArray startPng = TargetRenderer::renderToFile(startImgPath, QVector<Hit>());
        const QString relStartPath = QStringLiteral("images_lane%1/%2").arg(lane).arg(startImgName);

        QSqlQuery ins(m_db);
        ins.prepare(QStringLiteral(
            "INSERT INTO session_target(uuid,session_id,physical_target_id,target_number,status,start_time,start_image_path) "
            "VALUES(?,?,?,?,?,?,?)"));
        ins.addBindValue(stUuid);
        ins.addBindValue(sessionId);
        ins.addBindValue(ptId);
        ins.addBindValue(position);
        ins.addBindValue(QStringLiteral("ACTIVE"));
        ins.addBindValue(stStart);
        ins.addBindValue(relStartPath);
        ins.exec();

        SessionTargetRow st;
        st.targetId = ins.lastInsertId().toInt();
        st.sessionId = sessionId;
        st.uuid = stUuid;
        st.targetNumber = position;
        state.sessionTargets.append(st);

        const QJsonObject stPayload{
            {"uuid", stUuid}, {"session_uuid", sessUuid}, {"physical_target_uuid", ptUuid},
            {"target_number", position}, {"status", "ACTIVE"}, {"start_time", stStart},
            {"start_image_b64", QString::fromLatin1(startPng.toBase64())}};
        queueEvent(seed.edge.id, QStringLiteral("CREATE"), QStringLiteral("session_target"), stPayload);
    }
    return state;
}

ShotOutcome EdgeDatabase::fireShot(int edgeId, const SessionTargetRow &st, int shotNumber, int totalShots,
                                   int lane, const QString &imagesDir)
{
    QSqlQuery priorQ(m_db);
    priorQ.prepare(QStringLiteral("SELECT x_mm, y_mm, score FROM shot WHERE target_id=? ORDER BY shot_number"));
    priorQ.addBindValue(st.targetId);
    priorQ.exec();
    QVector<Hit> hits;
    while (priorQ.next()) {
        Hit h;
        h.xMm = priorQ.value(0).toDouble();
        h.yMm = priorQ.value(1).toDouble();
        h.score = priorQ.value(2).toDouble();
        hits.append(h);
    }

    auto *rng = QRandomGenerator::global();
    const double x = rng->generateDouble() * 360.0 - 180.0;
    const double y = rng->generateDouble() * 360.0 - 180.0;
    const double dist = std::sqrt(x * x + y * y);
    const double rawScore = 10.0 - dist / 25.0;
    const double score = std::max(0.0, std::round(rawScore * 10.0) / 10.0);

    Hit newHit;
    newHit.xMm = x;
    newHit.yMm = y;
    newHit.score = score;
    hits.append(newHit);

    const QString imgName = QStringLiteral("%1_%2.png").arg(st.uuid).arg(shotNumber, 2, 10, QChar('0'));
    const QString imgPath = imagesDir + QStringLiteral("/") + imgName;
    const QByteArray pngBytes = TargetRenderer::renderToFile(imgPath, hits);

    const QString shotUuid = newUuid();
    const QString ts = nowIso();
    const double xPixel = 450.0 + x / 0.5;
    const double yPixel = 450.0 - y / 0.5;
    const double xMmR = std::round(x * 100.0) / 100.0;
    const double yMmR = std::round(y * 100.0) / 100.0;
    const double distR = std::round(dist * 100.0) / 100.0;
    const double confidence = std::round((rng->generateDouble() * (0.999 - 0.96) + 0.96) * 1000.0) / 1000.0;
    const qint64 nanos = std::chrono::duration_cast<std::chrono::nanoseconds>(
                              std::chrono::system_clock::now().time_since_epoch())
                              .count();
    const QString frameId = QStringLiteral("SIM-%1").arg(nanos);
    const QString relImagePath = QStringLiteral("images_lane%1/%2").arg(lane).arg(imgName);

    QSqlQuery ins(m_db);
    ins.prepare(QStringLiteral(
        "INSERT INTO shot(uuid,target_id,shot_number,shot_timestamp,x_pixel,y_pixel,x_mm,y_mm,"
        "distance_mm,score,confidence,detection_method,image_path,frame_id) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)"));
    ins.addBindValue(shotUuid);
    ins.addBindValue(st.targetId);
    ins.addBindValue(shotNumber);
    ins.addBindValue(ts);
    ins.addBindValue(xPixel);
    ins.addBindValue(yPixel);
    ins.addBindValue(xMmR);
    ins.addBindValue(yMmR);
    ins.addBindValue(distR);
    ins.addBindValue(score);
    ins.addBindValue(confidence);
    ins.addBindValue(QStringLiteral("SIM_MOBILE_NET"));
    ins.addBindValue(relImagePath);
    ins.addBindValue(frameId);
    ins.exec();

    QSqlQuery updSt(m_db);
    updSt.prepare(QStringLiteral(
        "UPDATE session_target SET total_score=total_score+?, total_shots=total_shots+1 WHERE target_id=?"));
    updSt.addBindValue(score);
    updSt.addBindValue(st.targetId);
    updSt.exec();

    QSqlQuery updSess(m_db);
    updSess.prepare(QStringLiteral(
        "UPDATE shooting_session SET "
        "total_score=(SELECT COALESCE(SUM(total_score),0) FROM session_target WHERE session_id=shooting_session.session_id), "
        "total_shots=(SELECT COALESCE(SUM(total_shots),0) FROM session_target WHERE session_id=shooting_session.session_id) "
        "WHERE session_id=?"));
    updSess.addBindValue(st.sessionId);
    updSess.exec();

    const QString imgB64 = QString::fromLatin1(pngBytes.toBase64());
    const QJsonObject payload{
        {"uuid", shotUuid}, {"target_id", st.targetId}, {"target_uuid", st.uuid}, {"shot_number", shotNumber},
        {"shot_timestamp", ts}, {"x_pixel", xPixel}, {"y_pixel", yPixel}, {"x_mm", xMmR}, {"y_mm", yMmR},
        {"distance_mm", distR}, {"score", score}, {"confidence", confidence},
        {"detection_method", "SIM_MOBILE_NET"}, {"frame_id", frameId}, {"image_path", relImagePath},
        {"image_b64", imgB64},
    };
    queueEvent(edgeId, QStringLiteral("CREATE"), QStringLiteral("shot"), payload);

    if (shotNumber == totalShots) {
        const QString endTime = nowIso();
        QSqlQuery updDone(m_db);
        updDone.prepare(QStringLiteral("UPDATE session_target SET status='COMPLETED', end_time=? WHERE target_id=?"));
        updDone.addBindValue(endTime);
        updDone.addBindValue(st.targetId);
        updDone.exec();

        const QJsonObject donePayload{{"uuid", st.uuid}, {"status", "COMPLETED"}, {"end_time", endTime}};
        queueEvent(edgeId, QStringLiteral("UPDATE"), QStringLiteral("session_target"), donePayload);
    }

    ShotOutcome outcome;
    outcome.shotNumber = shotNumber;
    outcome.xMm = xMmR;
    outcome.yMm = yMmR;
    outcome.distanceMm = distR;
    outcome.score = score;
    outcome.pngBytes = pngBytes;
    return outcome;
}

void EdgeDatabase::completeSession(int edgeId, int sessionId, const QString &sessionUuid)
{
    const QString endTime = nowIso();
    QSqlQuery q(m_db);
    q.prepare(QStringLiteral("UPDATE shooting_session SET status='COMPLETED', end_time=? WHERE session_id=?"));
    q.addBindValue(endTime);
    q.addBindValue(sessionId);
    q.exec();

    const QJsonObject payload{{"uuid", sessionUuid}, {"status", "COMPLETED"}, {"end_time", endTime}};
    queueEvent(edgeId, QStringLiteral("UPDATE"), QStringLiteral("session"), payload);
}

QVector<PendingEvent> EdgeDatabase::fetchPendingEvents()
{
    QVector<PendingEvent> events;
    QSqlQuery q(m_db);
    q.prepare(QStringLiteral(
        "SELECT sync_id, entity_type, event_type, payload FROM sync_queue WHERE status='PENDING' ORDER BY sync_id"));
    q.exec();
    while (q.next()) {
        PendingEvent e;
        e.syncId = q.value(0).toLongLong();
        e.entityType = q.value(1).toString();
        e.eventType = q.value(2).toString();
        const QString payloadStr = q.value(3).toString();
        QJsonParseError perr{};
        const QJsonDocument doc = QJsonDocument::fromJson(payloadStr.toUtf8(), &perr);
        if (perr.error == QJsonParseError::NoError && doc.isObject())
            e.payload = doc.object();
        events.append(e);
    }
    return events;
}

void EdgeDatabase::markSent(const QVector<qint64> &syncIds)
{
    if (syncIds.isEmpty())
        return;
    const QString ts = nowIso();
    QSqlQuery q(m_db);
    q.prepare(QStringLiteral("UPDATE sync_queue SET status='SENT', sent_at=? WHERE sync_id=?"));
    for (qint64 id : syncIds) {
        q.bindValue(0, ts);
        q.bindValue(1, id);
        q.exec();
    }
}
