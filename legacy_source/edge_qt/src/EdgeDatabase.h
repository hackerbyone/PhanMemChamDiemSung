#pragma once
#include <QJsonObject>
#include <QSqlDatabase>
#include <QString>
#include <QVector>

#include "TargetRenderer.h"

// One outbound event read back from the local sync_queue table, ready to be
// sent to the server. Mirrors what edge.py's sync() builds from the same
// table: only entity_type/event_type/payload actually cross the wire.
struct PendingEvent
{
    qint64 syncId = 0;
    QString entityType;
    QString eventType;
    QJsonObject payload;
};

struct EdgeRow
{
    int id = 0;
    QString uuid;
    QString edgeCode;
    QString hostname;
    QString ipAddress;
    QString softwareVersion;
};

struct UnitRow
{
    int id = 0;
    QString uuid;
    QString code;
    QString name;
};

struct ShooterRow
{
    int id = 0;
    QString uuid;
    QString code;
    QString name;
};

struct LaneRow
{
    int id = 0;
    QString uuid;
    int number = 0;
    QString name;
};

struct MatchRow
{
    int id = 0;
    QString uuid;
    QString code;
    QString name;
};

struct SessionTargetRow
{
    int targetId = 0;  // local session_target.target_id (primary key)
    int sessionId = 0; // parent shooting_session.session_id
    QString uuid;
    int targetNumber = 0;
};

struct SeedResult
{
    EdgeRow edge;
    UnitRow unit;
    ShooterRow shooter;
    LaneRow lane;
    MatchRow match;
};

struct SessionState
{
    int sessionId = 0;
    QString sessionUuid;
    QVector<SessionTargetRow> sessionTargets; // ordered by target_number, already LIMITed to `targets`
};

// One shot's outcome, everything the GUI/log need to display it.
struct ShotOutcome
{
    int shotNumber = 0;
    double xMm = 0;
    double yMm = 0;
    double distanceMm = 0;
    double score = 0;
    QByteArray pngBytes;
};

// Wraps one edge's local SQLite database (one instance = one lane, exactly
// like edge.py's Edge class + its module-level helper functions). Every
// method here is a direct C++ port of the corresponding function in
// edge.py -- same table/column names, same natural-key upserts, same event
// payload shapes -- so this app is wire-compatible with server.py without
// any server-side changes.
//
// IMPORTANT (Qt threading rule): a QSqlDatabase connection may only be used
// from the thread that created it. This class is always opened and used
// from EdgeWorker's background QThread, never from the GUI thread.
class EdgeDatabase
{
public:
    // Opens (creating if needed) the sqlite file at dbPath under a
    // dedicated connection named `connectionName`, turns on foreign key
    // enforcement, and creates every table (loaded from the shared
    // ../schema.sql, see findSchemaPath()) if they don't exist yet.
    bool open(const QString &dbPath, const QString &connectionName);
    QString lastError() const { return m_lastError; }

    // --- seeding (mirrors edge.py's get_or_create_edge_row() + seed()) ---
    EdgeRow getOrCreateEdgeRow(const QString &edgeCode);
    UnitRow upsertUnit(int edgeId, const QString &unitCode, const QString &unitName, const QString &unitType);
    ShooterRow upsertShooter(int edgeId, int unitId, const QString &unitUuid, const QString &shooterCode, const QString &shooterName);
    LaneRow upsertLane(int edgeId, const QString &edgeUuid, int laneNumber, const QString &laneName);
    // Upserts physical_target rows 1..targets for this lane and queues a
    // CREATE event for each one.
    void upsertPhysicalTargets(int edgeId, int laneId, const QString &laneUuid, int lane, int targets);
    MatchRow createMatch(int edgeId, int lane);

    // Full seed sequence for this lane: edge_device, unit, shooter, lane,
    // physical_target x targets, a fresh shooting_match. `shooterCode`
    // empty => default "S<lane:02d>"; `unitCode`/`unitName` empty => default
    // "UNIT-LANE<lane>" / "Lane <lane> Unit" -- pass non-empty values (e.g.
    // from a roster entry) to give this session's shooter a unit that is
    // independent of which lane happens to run it.
    SeedResult seedTopology(const QString &edgeCode, int lane, int targets,
                             const QString &shooterName, const QString &shooterCode,
                             const QString &unitCode = QString(), const QString &unitName = QString());

    // --- one session (mirrors edge.py's _start_session()) ---
    // Also renders and queues a blank "start" image (zero hits) for each
    // session_target, stored under imagesDir and sent as start_image_b64 so
    // the server/viewer can show it next to the shooting/finished image.
    SessionState startSession(const SeedResult &seed, int lane, int targets, const QString &imagesDir);

    // Fires shot number `shotNumber` (1-based) at session target `st`,
    // rendering the target image into `imagesDir`/<target uuid>_<NN>.png.
    // If shotNumber == totalShots, also marks that session_target COMPLETED
    // and queues its UPDATE event (same ordering as edge.py: both events
    // are queued in the same local transaction, before the caller syncs).
    ShotOutcome fireShot(int edgeId, const SessionTargetRow &st, int shotNumber, int totalShots,
                          int lane, const QString &imagesDir);

    void completeSession(int edgeId, int sessionId, const QString &sessionUuid);

    // --- outbound queue (mirrors edge.py's sync()) ---
    QVector<PendingEvent> fetchPendingEvents();
    void markSent(const QVector<qint64> &syncIds);

private:
    void queueEvent(int edgeId, const QString &eventType, const QString &entityType, const QJsonObject &payload);
    static QString nowIso();
    static QString newUuid();
    // Locates the shared schema.sql on disk: tries next to the executable,
    // then 1-4 directories above it (covers both a flat MinGW build output
    // and Qt Creator's more deeply nested per-kit/per-config MSVC shadow
    // build folders), then falls back to the absolute path the project was
    // built from (SCHEMA_SQL_SOURCE_PATH, injected by CMakeLists.txt) --
    // useful if the .exe gets copied somewhere without its schema.sql
    // neighbor. Returns an empty string if none of those exist.
    static QString findSchemaPath();

    QSqlDatabase m_db;
    QString m_connectionName;
    QString m_lastError;
};
