#include "EdgeWorker.h"
#include "NamePool.h"
#include "SyncClient.h"

#include <QCoreApplication>
#include <QDir>
#include <QJsonArray>
#include <QJsonObject>

#include <algorithm>
#include <utility>

EdgeWorker::EdgeWorker(EdgeConfig config, QObject *parent)
    : QThread(parent)
    , m_config(std::move(config))
{
}

void EdgeWorker::sleepSeconds(double seconds)
{
    qint64 remainingMs = static_cast<qint64>(seconds * 1000.0);
    const qint64 chunkMs = 50; // so requestStop() takes effect within ~50ms, not after the whole delay
    while (remainingMs > 0 && !m_stopRequested.load()) {
        const qint64 step = std::min(chunkMs, remainingMs);
        QThread::msleep(static_cast<unsigned long>(step));
        remainingMs -= step;
    }
}

void EdgeWorker::run()
{
    m_stopRequested.store(false);

    m_resolvedDbPath = m_config.dbPath.isEmpty()
        ? QCoreApplication::applicationDirPath() + QStringLiteral("/edge%1.db").arg(m_config.lane)
        : m_config.dbPath;
    m_resolvedEdgeCode = m_config.edgeCode.isEmpty()
        ? QStringLiteral("EDGE-LANE%1").arg(m_config.lane)
        : m_config.edgeCode;
    m_resolvedImagesDir = QCoreApplication::applicationDirPath() + QStringLiteral("/images_lane%1").arg(m_config.lane);
    QDir().mkpath(m_resolvedImagesDir);

    EdgeDatabase db;
    // A unique connection name per run so re-starting this worker (or
    // running two EdgeWorkers, e.g. in a future extension) never collides
    // with QSqlDatabase's process-wide connection registry.
    const QString connName = QStringLiteral("edge_conn_%1")
                                  .arg(reinterpret_cast<quintptr>(QThread::currentThreadId()));
    if (!db.open(m_resolvedDbPath, connName)) {
        emit errorOccurred(QStringLiteral("Failed to open local database %1: %2")
                                .arg(m_resolvedDbPath, db.lastError()));
        emit allDone();
        return;
    }
    emit logMessage(QStringLiteral("Lane %1 | local db: %2").arg(m_config.lane).arg(m_resolvedDbPath));

    if (!m_config.roster.isEmpty()) {
        // Roster mode: a named list of shooters, each with their OWN unit,
        // run in sequence on this one lane (mirrors edge.py's
        // run_roster_sequence()). Takes precedence over --people when both
        // are set -- this is the GUI-driven "add shooters, generate list,
        // Start follows it" feature, and the fix for "shooters in one unit
        // may shoot at different lanes": each entry's unit travels WITH the
        // shooter, never implied by the lane number.
        const QVector<RosterEntry> &roster = m_config.roster;
        const int n = roster.size();
        for (int i = 0; i < n; ++i) {
            if (m_stopRequested.load())
                break;
            const RosterEntry &entry = roster.at(i);
            const QString name = entry.name.isEmpty() ? defaultShooterName(m_config.lane) : entry.name;
            const QString shooterCode = entry.shooterCode.isEmpty()
                ? QStringLiteral("S%1-%2").arg(m_config.lane, 2, 10, QChar('0')).arg(i + 1, 2, 10, QChar('0'))
                : entry.shooterCode;
            emit rosterEntryStarted(i + 1, n, name, shooterCode, entry.unitCode, entry.unitName);
            emit logMessage(QStringLiteral("Lane %1 | ==== Roster %2/%3: %4 (%5)%6 steps up ====")
                                 .arg(m_config.lane)
                                 .arg(i + 1)
                                 .arg(n)
                                 .arg(name, shooterCode,
                                      entry.unitName.isEmpty() ? QString() : QStringLiteral(" / ") + entry.unitName));
            runOneSession(db, name, shooterCode, entry.unitCode, entry.unitName);
            if (i < n - 1 && !m_stopRequested.load())
                sleepSeconds(m_config.personInterval);
        }
        emit logMessage(QStringLiteral("Lane %1 | Roster sequence complete: %2 shooters scored.")
                             .arg(m_config.lane)
                             .arg(n));
    } else if (m_config.peopleCount > 0) {
        // --people N: N shooters take turns on this one lane, one full
        // session each, back to back (mirrors edge.py's run_people_sequence()).
        QStringList names = m_config.peopleNames;
        const QStringList &pool = vnShooterNames();
        while (names.size() < m_config.peopleCount)
            names.append(pool.at(names.size() % pool.size()));

        for (int i = 1; i <= m_config.peopleCount; ++i) {
            if (m_stopRequested.load())
                break;
            const QString personName = names.at(i - 1);
            const QString shooterCode = QStringLiteral("S%1-%2")
                                             .arg(m_config.lane, 2, 10, QChar('0'))
                                             .arg(i, 2, 10, QChar('0'));
            emit personStarted(i, m_config.peopleCount, personName, shooterCode);
            emit logMessage(QStringLiteral("Lane %1 | ==== Person %2/%3: %4 (%5) steps up ====")
                                 .arg(m_config.lane)
                                 .arg(i)
                                 .arg(m_config.peopleCount)
                                 .arg(personName, shooterCode));
            runOneSession(db, personName, shooterCode, m_config.unitCode, m_config.unitName);
            if (i < m_config.peopleCount && !m_stopRequested.load())
                sleepSeconds(m_config.personInterval);
        }
        emit logMessage(QStringLiteral("Lane %1 | Evaluation sequence complete: %2 people scored.")
                             .arg(m_config.lane)
                             .arg(m_config.peopleCount));
    } else {
        // Single shooter on this lane (default shooter_code "S<lane:02d>",
        // applied inside EdgeDatabase::seedTopology when shooterCode is empty).
        runOneSession(db, m_config.shooterName, m_config.shooterCode, m_config.unitCode, m_config.unitName);
    }

    emit allDone();
}

void EdgeWorker::runOneSession(EdgeDatabase &db, const QString &shooterName, const QString &shooterCode,
                                const QString &unitCode, const QString &unitName)
{
    const SeedResult seed = db.seedTopology(m_resolvedEdgeCode, m_config.lane, m_config.targets,
                                             shooterName, shooterCode, unitCode, unitName);
    m_edgeRow = seed.edge;
    emit seeded(seed.edge.uuid, seed.edge.edgeCode, m_resolvedDbPath);
    emit logMessage(QStringLiteral("Lane %1 | EDGE UUID: %2 | Shooter: %3 | Unit: %4 | db=%5")
                         .arg(m_config.lane)
                         .arg(seed.edge.uuid, seed.shooter.name, seed.unit.name, m_resolvedDbPath));

    const SessionState session = db.startSession(seed, m_config.lane, m_config.targets, m_resolvedImagesDir);
    emit sessionStarted(session.sessionId, session.sessionUuid, session.sessionTargets.size());

    // Push topology + "session started" status before any shots exist.
    syncNow(db, seed.edge);

    const int targetCount = session.sessionTargets.size();
    for (int ti = 0; ti < targetCount; ++ti) {
        if (m_stopRequested.load())
            break;
        const SessionTargetRow &st = session.sessionTargets.at(ti);
        for (int n = 1; n <= m_config.shots; ++n) {
            if (m_stopRequested.load())
                break;
            const ShotOutcome shot = db.fireShot(seed.edge.id, st, n, m_config.shots, m_config.lane, m_resolvedImagesDir);
            emit shotFired(st.targetNumber, n, m_config.shots, shot.xMm, shot.yMm, shot.score, shot.pngBytes);
            syncNow(db, seed.edge); // push THIS shot now, before the next shot fires
            emit logMessage(QStringLiteral("Lane %1 | Target %2: shot %3/%4")
                                 .arg(m_config.lane)
                                 .arg(st.targetNumber)
                                 .arg(n)
                                 .arg(m_config.shots));
            if (n < m_config.shots && !m_stopRequested.load())
                sleepSeconds(m_config.interval);
        }
        emit targetCompleted(st.targetNumber, ti + 1, targetCount);
        if (ti < targetCount - 1 && !m_stopRequested.load()) {
            emit logMessage(QStringLiteral("Lane %1 | Target %2 complete -- moving to next target")
                                 .arg(m_config.lane)
                                 .arg(st.targetNumber));
            sleepSeconds(m_config.targetInterval);
        }
    }

    db.completeSession(seed.edge.id, session.sessionId, session.sessionUuid);
    syncNow(db, seed.edge);
    emit sessionCompleted(session.sessionId);
    emit logMessage(QStringLiteral("Lane %1 | Simulation complete.").arg(m_config.lane));
}

void EdgeWorker::syncNow(EdgeDatabase &db, const EdgeRow &edgeRow)
{
    const QVector<PendingEvent> pending = db.fetchPendingEvents();
    if (pending.isEmpty())
        return;

    QJsonArray eventsArr;
    QVector<qint64> ids;
    ids.reserve(pending.size());
    for (const PendingEvent &e : pending) {
        const QJsonObject ev{{"entity_type", e.entityType}, {"event_type", e.eventType}, {"payload", e.payload}};
        eventsArr.append(ev);
        ids.append(e.syncId);
    }
    const QJsonObject body{
        {"edge_uuid", edgeRow.uuid},
        {"edge_code", edgeRow.edgeCode},
        {"hostname", edgeRow.hostname},
        {"ip_address", edgeRow.ipAddress},
        {"software_version", edgeRow.softwareVersion},
        {"events", eventsArr},
    };

    SyncClient client; // created (and destroyed) on this worker thread, per Qt's threading rules for QNetworkAccessManager
    const SyncClient::Result res = client.postSync(m_config.server, body);
    if (res.ok) {
        db.markSent(ids);
        const int accepted = res.responseJson.value(QStringLiteral("accepted")).toInt();
        const int skippedCount = res.responseJson.value(QStringLiteral("skipped")).toArray().size();
        emit syncResult(true, accepted, skippedCount, QString());
        emit logMessage(QStringLiteral("Lane %1 | sync: accepted=%2 skipped=%3")
                             .arg(m_config.lane)
                             .arg(accepted)
                             .arg(skippedCount));
    } else {
        // Matches edge.py's behavior on a failed sync: the events stay
        // PENDING in the local queue and are retried on the next sync call
        // (the next shot, or the next session-completion push), so nothing
        // is lost even if the server is briefly unreachable.
        emit syncResult(false, 0, 0, res.errorString);
        emit logMessage(QStringLiteral("Lane %1 | LAN unavailable; keeping local queue: %2")
                             .arg(m_config.lane)
                             .arg(res.errorString));
    }
}
