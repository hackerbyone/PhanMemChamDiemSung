#pragma once
#include <QString>
#include <QStringList>
#include <QVector>

// One roster entry: a named shooter with their OWN unit, used by the roster
// GUI (and the optional --roster file) to evaluate several shooters -- who
// may belong to DIFFERENT units -- back to back on one lane. This is the
// fix for "shooters in one unit may shoot at different lanes": a session's
// unit always comes from the entry that produced it, never implicitly from
// the lane number, so results attribute to the real unit no matter which
// lane happens to run the entry. Any blank field falls back to the usual
// lane-derived default (same rule as --shooter-code/--unit-code/--unit-name).
struct RosterEntry
{
    QString name;
    QString unitCode;
    QString unitName;
    QString shooterCode;
};

// All the knobs edge.py accepts on the command line, minus --lanes/
// --sequential (this app is always "one process = one lane", matching the
// production deployment model of edge.py: launch one edge process per lane,
// each with a different --lane). Use --people for the multi-shooter-on-
// one-lane mode, exactly like edge.py's --people, or `roster` for a named
// list of shooters (each with their own unit) run in sequence -- `roster`
// takes precedence over `peopleCount` when both are non-empty.
struct EdgeConfig
{
    QString server = QStringLiteral("http://127.0.0.1:8000");
    int lane = 1;
    int targets = 2;
    int shots = 10;
    double interval = 0.5;       // seconds between shots on the same target
    double targetInterval = 1.0; // seconds between targets
    QString dbPath;              // empty => default "edge<lane>.db" next to the executable
    QString edgeCode;            // empty => default "EDGE-LANE<lane>"
    QString shooterName;         // empty => default VN name picked by lane (single-lane mode only)
    QString shooterCode;         // empty => default "S<lane:02d>" (single-lane mode only)
    QString unitCode;            // empty => default "UNIT-LANE<lane>" (single-lane / --people, shared by all)
    QString unitName;            // empty => default "Lane <lane> Unit"
    int peopleCount = 0;         // 0 = disabled (single shooter on this lane); >0 = --people mode
    QStringList peopleNames;     // optional override names for --people, VN pool fills any gap
    double personInterval = 2.0; // seconds between people in --people mode / roster entries
    QVector<RosterEntry> roster; // non-empty => roster mode: run each entry as its own session, in order
    bool autostart = true;       // start the simulation as soon as the window opens
};
