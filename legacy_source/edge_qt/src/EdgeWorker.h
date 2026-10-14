#pragma once
#include <QThread>
#include <atomic>

#include "EdgeConfig.h"
#include "EdgeDatabase.h"

// Runs the whole simulation for this edge/lane on a background thread, so
// the GUI stays responsive during the pacing delays between shots/targets/
// people (edge.py simply blocks with time.sleep() since it has no GUI to
// keep alive; this is the GUI-safe equivalent of that same control flow).
//
// The control flow is a close, function-by-function port of edge.py:
//   seedTopology()         ~ seed()
//   runOneSession()        ~ _start_session() + the per-target shot loop
//                             + _complete_session(), all three inlined
//                             into one function like edge.py's run()
//   run()  /  runPeople()  ~ run() (single shooter)  /  run_people_sequence()
//
// One EdgeWorker = one lane, one shooter sequence (single shooter, or
// --people N shooters back to back) -- never multiple lanes. To simulate
// several lanes, launch several edge_qt processes, each with a different
// --lane, exactly like edge.py's own recommended deployment.
class EdgeWorker : public QThread
{
    Q_OBJECT
public:
    explicit EdgeWorker(EdgeConfig config, QObject *parent = nullptr);

    // Cooperative stop: checked between shots/targets/people. Does not try
    // to abort mid-shot or mid-network-call; the run simply stops advancing
    // at the next safe boundary and the thread finishes.
    void requestStop() { m_stopRequested.store(true); }

signals:
    void logMessage(const QString &text);
    void errorOccurred(const QString &message);

    // Topology is ready; dbPath is where the local sqlite file actually is.
    void seeded(const QString &edgeUuid, const QString &edgeCode, const QString &dbPath);

    // --people mode only, fired once per person before their session starts.
    void personStarted(int index, int total, const QString &name, const QString &shooterCode);

    // Roster mode only, fired once per roster entry before their session
    // starts. unitCode/unitName are the entry's RAW (possibly empty) fields
    // -- empty means "falls back to the lane-derived default", same as the
    // rest of this app's unit overrides.
    void rosterEntryStarted(int index, int total, const QString &name, const QString &shooterCode,
                             const QString &unitCode, const QString &unitName);

    void sessionStarted(int sessionId, const QString &sessionUuid, int targetCount);
    void shotFired(int targetPosition, int shotNumber, int totalShots, double xMm, double yMm,
                   double score, const QByteArray &pngBytes);
    void targetCompleted(int targetPosition, int targetIndex, int targetCount);
    void sessionCompleted(int sessionId);

    // One /api/sync round-trip result (accepted/skipped counts as reported
    // by server.py, or ok=false with `message` explaining a network/timeout
    // failure -- matching edge.py's "LAN unavailable; keeping local queue").
    void syncResult(bool ok, int accepted, int skipped, const QString &message);

    void allDone();

protected:
    void run() override;

private:
    // One full session for one shooter on this->m_config.lane: seed (only
    // actually creates rows the first time; subsequent calls upsert and
    // reuse the same lane/unit identity, exactly like edge.py's seed()),
    // open a session + its session_targets, fire every target's full shot
    // string in order, then mark the session COMPLETED. Used directly for
    // the single-shooter mode and once per person/roster-entry in --people/
    // roster mode. unitCode/unitName empty => lane-derived default.
    void runOneSession(EdgeDatabase &db, const QString &shooterName, const QString &shooterCode,
                        const QString &unitCode, const QString &unitName);

    // Reads every PENDING row out of the local sync_queue and POSTs them to
    // the server in one batch, marking exactly those rows SENT on success
    // (matching edge.py's sync(): a failure just leaves them PENDING for
    // the next call to retry, so nothing is ever lost).
    void syncNow(EdgeDatabase &db, const EdgeRow &edgeRow);

    void sleepSeconds(double seconds);

    EdgeConfig m_config;
    std::atomic<bool> m_stopRequested{false};

    // Resolved once at the top of run() so every helper can just read them.
    QString m_resolvedDbPath;
    QString m_resolvedEdgeCode;
    QString m_resolvedImagesDir;
    EdgeRow m_edgeRow; // filled in by the first seedTopology() call in run()
};
