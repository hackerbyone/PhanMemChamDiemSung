#include <QApplication>
#include <QCommandLineOption>
#include <QCommandLineParser>
#include <QCoreApplication>
#include <QDebug>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QStringConverter>
#include <QTextStream>

#include "EdgeConfig.h"
#include "MainWindow.h"

// Loads a roster of {name, unit_code, unit_name, shooter_code} entries from
// a CSV or JSON file, to preload the GUI's roster table (see --roster
// below and MainWindow's constructor) -- mirrors edge.py's load_roster().
// CSV parsing here is intentionally simple (split on ',', no quoted-field
// support) since this is a simulator's convenience loader, not a general
// CSV library; use JSON if a name/unit ever needs to contain a comma.
static QVector<RosterEntry> loadRosterFile(const QString &path)
{
    QVector<RosterEntry> entries;
    const bool isJson = QFileInfo(path).suffix().compare(QStringLiteral("json"), Qt::CaseInsensitive) == 0;
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly | QIODevice::Text)) {
        qWarning("--roster: could not open %s", qPrintable(path));
        return entries;
    }
    if (isJson) {
        const QJsonDocument doc = QJsonDocument::fromJson(file.readAll());
        for (const QJsonValue &v : doc.array()) {
            const QJsonObject o = v.toObject();
            RosterEntry e;
            e.name = o.value(QStringLiteral("name")).toString();
            e.unitCode = o.value(QStringLiteral("unit_code")).toString();
            e.unitName = o.value(QStringLiteral("unit_name")).toString();
            e.shooterCode = o.value(QStringLiteral("shooter_code")).toString();
            entries.append(e);
        }
    } else {
        QTextStream in(&file);
        in.setEncoding(QStringConverter::Utf8);
        QStringList header;
        bool first = true;
        while (!in.atEnd()) {
            const QString line = in.readLine();
            if (line.trimmed().isEmpty())
                continue;
            const QStringList cols = line.split(QLatin1Char(','));
            if (first) {
                for (const QString &c : cols)
                    header.append(c.trimmed().toLower());
                first = false;
                continue;
            }
            RosterEntry e;
            for (int i = 0; i < header.size() && i < cols.size(); ++i) {
                const QString val = cols.at(i).trimmed();
                if (header.at(i) == QStringLiteral("name")) e.name = val;
                else if (header.at(i) == QStringLiteral("unit_code")) e.unitCode = val;
                else if (header.at(i) == QStringLiteral("unit_name")) e.unitName = val;
                else if (header.at(i) == QStringLiteral("shooter_code")) e.shooterCode = val;
            }
            entries.append(e);
        }
    }
    return entries;
}

// Qt/C++ edge simulator. Convention: edge 1 simulates lane 1, edge 2
// simulates lane 2, ..., edge N simulates lane N -- one process per lane,
// exactly like edge.py's own recommended deployment (run several processes,
// each with a different --lane). To run several lanes, launch this
// executable several times with a different --lane each time, e.g.:
//
//   edge_qt --server http://SERVER:8000 --lane 1 --targets 2 --shots 10
//   edge_qt --server http://SERVER:8000 --lane 2 --targets 2 --shots 10
//
// or evaluate several people on ONE lane with --people, exactly like
// edge.py's --people:
//
//   edge_qt --server http://SERVER:8000 --lane 1 --people 4 --targets 2 --shots 10
int main(int argc, char *argv[])
{
    QApplication app(argc, argv);
    QApplication::setApplicationName(QStringLiteral("edge_qt"));
    QApplication::setApplicationVersion(QStringLiteral("1.0.0"));

    QCommandLineParser parser;
    parser.setApplicationDescription(QStringLiteral(
        "Qt/C++ edge simulator -- one process simulates ONE lane (edge N = lane N), "
        "mirroring edge.py's --lane / --people modes."));
    parser.addHelpOption();
    parser.addVersionOption();

    QCommandLineOption serverOpt(QStringList() << QStringLiteral("server"),
                                  QStringLiteral("Server base URL."), QStringLiteral("url"),
                                  QStringLiteral("http://127.0.0.1:8000"));
    QCommandLineOption laneOpt(QStringList() << QStringLiteral("lane"),
                                QStringLiteral("Lane number this edge instance simulates."), QStringLiteral("n"),
                                QStringLiteral("1"));
    QCommandLineOption targetsOpt(QStringList() << QStringLiteral("targets"),
                                   QStringLiteral("Number of physical targets on this lane."), QStringLiteral("n"),
                                   QStringLiteral("2"));
    QCommandLineOption shotsOpt(QStringList() << QStringLiteral("shots"),
                                 QStringLiteral("Shots fired at each target before moving to the next."), QStringLiteral("n"),
                                 QStringLiteral("10"));
    QCommandLineOption intervalOpt(QStringList() << QStringLiteral("interval"),
                                    QStringLiteral("Delay in seconds between consecutive shots on the same target."),
                                    QStringLiteral("sec"), QStringLiteral("0.5"));
    QCommandLineOption targetIntervalOpt(QStringList() << QStringLiteral("target-interval"),
                                          QStringLiteral("Pause in seconds after a target's full string, before the next target."),
                                          QStringLiteral("sec"), QStringLiteral("1.0"));
    QCommandLineOption dbOpt(QStringList() << QStringLiteral("db"),
                              QStringLiteral("Override local sqlite db path (default: edge<N>.db next to the executable)."),
                              QStringLiteral("path"));
    QCommandLineOption edgeCodeOpt(QStringList() << QStringLiteral("edge-code"),
                                    QStringLiteral("Override edge_code (default: EDGE-LANE<N>)."), QStringLiteral("code"));
    QCommandLineOption shooterNameOpt(QStringList() << QStringLiteral("shooter-name"),
                                       QStringLiteral("Shooter's name (default: a Vietnamese name picked by lane number)."),
                                       QStringLiteral("name"));
    QCommandLineOption shooterCodeOpt(QStringList() << QStringLiteral("shooter-code"),
                                       QStringLiteral("Override shooter_code (default: \"S<lane:02d>\"). Single-shooter mode only."),
                                       QStringLiteral("code"));
    QCommandLineOption unitCodeOpt(QStringList() << QStringLiteral("unit-code"),
                                    QStringLiteral("Override unit_code (default: \"UNIT-LANE<lane>\"). Lets a shooter's "
                                                    "unit be set independently of the lane. Ignored with --roster."),
                                    QStringLiteral("code"));
    QCommandLineOption unitNameOpt(QStringList() << QStringLiteral("unit-name"),
                                    QStringLiteral("Override unit_name (default: \"Lane <lane> Unit\"). Same scoping as --unit-code."),
                                    QStringLiteral("name"));
    QCommandLineOption rosterOpt(QStringList() << QStringLiteral("roster"),
                                  QStringLiteral("Path to a CSV or JSON roster file of {name, unit_code, unit_name, "
                                                  "shooter_code} entries, preloaded into the GUI's roster table -- "
                                                  "each can carry its own unit. Takes precedence over --people."),
                                  QStringLiteral("path"));
    QCommandLineOption peopleOpt(QStringList() << QStringLiteral("people"),
                                  QStringLiteral("Evaluate N shooters sequentially on this ONE lane, one after another."),
                                  QStringLiteral("n"));
    QCommandLineOption peopleNamesOpt(QStringList() << QStringLiteral("people-names"),
                                       QStringLiteral("Comma-separated names for --people, e.g. \"An,Binh,Chi,Dung\"."),
                                       QStringLiteral("names"));
    QCommandLineOption personIntervalOpt(QStringList() << QStringLiteral("person-interval"),
                                          QStringLiteral("Pause in seconds between people, with --people."),
                                          QStringLiteral("sec"), QStringLiteral("2.0"));
    QCommandLineOption noAutostartOpt(QStringList() << QStringLiteral("no-autostart"),
                                       QStringLiteral("Don't start the simulation automatically; wait for the Start button."));
    QCommandLineOption resetOpt(QStringList() << QStringLiteral("reset"),
                                 QStringLiteral("TESTING: delete this lane's local data (the sqlite db and rendered "
                                                 "images folder next to the executable) then exit immediately -- "
                                                 "no window opens and every other option is ignored. Does NOT touch "
                                                 "the server; see POST /api/admin/reset on server.py for that. The "
                                                 "GUI's own reset button (next to Start/Stop) does the same thing "
                                                 "for a running session."));

    parser.addOption(serverOpt);
    parser.addOption(laneOpt);
    parser.addOption(targetsOpt);
    parser.addOption(shotsOpt);
    parser.addOption(intervalOpt);
    parser.addOption(targetIntervalOpt);
    parser.addOption(dbOpt);
    parser.addOption(edgeCodeOpt);
    parser.addOption(shooterNameOpt);
    parser.addOption(shooterCodeOpt);
    parser.addOption(unitCodeOpt);
    parser.addOption(unitNameOpt);
    parser.addOption(rosterOpt);
    parser.addOption(peopleOpt);
    parser.addOption(peopleNamesOpt);
    parser.addOption(personIntervalOpt);
    parser.addOption(noAutostartOpt);
    parser.addOption(resetOpt);

    parser.process(app);

    if (parser.isSet(resetOpt)) {
        const int lane = parser.value(laneOpt).toInt();
        const QString dbPath = parser.isSet(dbOpt)
            ? parser.value(dbOpt)
            : QCoreApplication::applicationDirPath() + QStringLiteral("/edge%1.db").arg(lane);
        const QString imagesDir = QCoreApplication::applicationDirPath() + QStringLiteral("/images_lane%1").arg(lane);
        QStringList removed;
        if (QFile::exists(dbPath)) {
            if (QFile::remove(dbPath))
                removed << dbPath;
            else
                qWarning("Could not remove %s", qPrintable(dbPath));
        }
        QDir imgDir(imagesDir);
        if (imgDir.exists()) {
            if (imgDir.removeRecursively())
                removed << imagesDir;
            else
                qWarning("Could not remove %s", qPrintable(imagesDir));
        }
        if (removed.isEmpty())
            qInfo("[edge_qt] Lane %d: nothing to reset (no local data found at %s).", lane, qPrintable(dbPath));
        else
            qInfo("[edge_qt] Lane %d: reset -- removed %s.", lane, qPrintable(removed.join(QStringLiteral(", "))));
        return 0;
    }

    EdgeConfig config;
    config.server = parser.value(serverOpt);
    config.lane = parser.value(laneOpt).toInt();
    config.targets = parser.value(targetsOpt).toInt();
    config.shots = parser.value(shotsOpt).toInt();
    config.interval = parser.value(intervalOpt).toDouble();
    config.targetInterval = parser.value(targetIntervalOpt).toDouble();
    config.dbPath = parser.value(dbOpt);
    config.edgeCode = parser.value(edgeCodeOpt);
    config.shooterName = parser.value(shooterNameOpt);
    config.shooterCode = parser.value(shooterCodeOpt);
    config.unitCode = parser.value(unitCodeOpt);
    config.unitName = parser.value(unitNameOpt);
    if (parser.isSet(rosterOpt))
        config.roster = loadRosterFile(parser.value(rosterOpt));
    if (parser.isSet(peopleOpt))
        config.peopleCount = parser.value(peopleOpt).toInt();
    if (parser.isSet(peopleNamesOpt)) {
        const QStringList raw = parser.value(peopleNamesOpt).split(QLatin1Char(','));
        for (const QString &s : raw) {
            const QString trimmed = s.trimmed();
            if (!trimmed.isEmpty())
                config.peopleNames.append(trimmed);
        }
    }
    config.personInterval = parser.value(personIntervalOpt).toDouble();
    config.autostart = !parser.isSet(noAutostartOpt);

    MainWindow window(config);
    window.show();
    return QApplication::exec();
}
