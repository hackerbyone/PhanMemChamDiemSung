#include "MainWindow.h"
#include "NamePool.h"
#include "VnLabels.h"
#include "ui_MainWindow.h"

#include <QAbstractItemView>
#include <QCoreApplication>
#include <QDir>
#include <QFile>
#include <QFont>
#include <QFrame>
#include <QGroupBox>
#include <QHBoxLayout>
#include <QHeaderView>
#include <QLabel>
#include <QMessageBox>
#include <QPixmap>
#include <QPlainTextEdit>
#include <QProgressBar>
#include <QPushButton>
#include <QSet>
#include <QSpinBox>
#include <QTableWidget>
#include <QTableWidgetItem>
#include <QTimer>
#include <QVBoxLayout>
#include <QWidget>

#include <algorithm>
#include <utility>

MainWindow::MainWindow(EdgeConfig config, QWidget *parent)
    : QMainWindow(parent)
    , m_config(std::move(config))
{
    buildUi();
    // The .ui form's laneSpinBox always starts at 1; set it to whatever
    // --lane actually resolved to without re-triggering onLaneChanged()
    // (which would be redundant with the refreshInfoLabel() call below, and
    // would fire before m_worker/etc. are ready during construction).
    m_laneSpinBox->blockSignals(true);
    m_laneSpinBox->setValue(m_config.lane);
    m_laneSpinBox->blockSignals(false);
    refreshInfoLabel();
    // Preload the roster table from --roster, if given; the user can still
    // edit/add/remove rows before Start (which rebuilds m_config.roster
    // fresh from the table, so this initial copy is just a starting point).
    for (const RosterEntry &e : std::as_const(m_config.roster))
        addRosterRow(e.name, e.unitName, e.unitCode, e.shooterCode);
    if (m_config.peopleCount > 0) {
        m_personLabel->setText(QStringLiteral("--people %1").arg(m_config.peopleCount));
        m_personLabel->show();
    }

    if (m_config.autostart)
        QTimer::singleShot(0, this, &MainWindow::onStartClicked);
}

MainWindow::~MainWindow()
{
    // A QThread must not be destroyed while still running -- ask it to stop
    // and give it a moment to unwind at its next safe boundary. If it has
    // already finished on its own, m_worker (a QPointer) is already null
    // here and this is a no-op.
    if (m_worker) {
        m_worker->requestStop();
        m_worker->wait(3000);
    }
    delete ui;
}

void MainWindow::buildUi()
{
    // Widget hierarchy, sizes, ranges and every plain-ASCII static string
    // now live in MainWindow.ui (edited in Qt Designer, compiled by AUTOUIC
    // into ui_MainWindow.h). This function aliases the m_xxx member
    // pointers used throughout this file onto ui->xxx, then fills in what
    // the .ui form deliberately leaves out: Vietnamese text (kept as \xHH
    // QStringLiteral escapes via VnLabels, never embedded in the .ui XML,
    // to avoid non-ASCII source-encoding issues in uic-generated code),
    // table-header behavior that isn't a simple .ui property, and the
    // signal/slot connections.
    ui = new Ui::MainWindow();
    ui->setupUi(this);

    QFont titleFont = ui->titleLabel->font();
    titleFont.setPointSize(titleFont.pointSize() + 4);
    titleFont.setBold(true);
    ui->titleLabel->setFont(titleFont);

    m_laneSpinBox = ui->laneSpinBox;
    m_infoLabel = ui->infoLabel;
    m_personLabel = ui->personLabel;

    // Roster (shooter list) group: add/generate a list of {name, unit}
    // entries; a non-empty table at Start time makes the simulation run
    // through it in order instead of the single-shooter/--people modes
    // (see EdgeWorker::run()'s roster branch and onStartClicked() below).
    ui->rosterGroup->setTitle(VnLabels::rosterTitle());

    m_rosterTable = ui->rosterTable;
    m_rosterTable->setHorizontalHeaderLabels({VnLabels::colName(), VnLabels::colUnitName(),
                                               VnLabels::colUnitCode(), VnLabels::colShooterCode()});
    m_rosterTable->horizontalHeader()->setStretchLastSection(true);
    m_rosterTable->horizontalHeader()->setSectionResizeMode(0, QHeaderView::Stretch);
    m_rosterTable->horizontalHeader()->setSectionResizeMode(1, QHeaderView::Stretch);

    m_addRosterBtn = ui->addRosterBtn;
    m_addRosterBtn->setText(VnLabels::addRow());
    m_removeRosterBtn = ui->removeRosterBtn;
    m_removeRosterBtn->setText(VnLabels::removeRow());
    ui->genShooterCountLabel->setText(VnLabels::genShooterCountLabel());
    m_genShooterCount = ui->genShooterCount;
    ui->genUnitCountLabel->setText(VnLabels::genUnitCountLabel());
    m_genUnitCount = ui->genUnitCount;
    m_generateRosterBtn = ui->generateRosterBtn;
    m_generateRosterBtn->setText(VnLabels::generate());
    ui->rosterHint->setText(VnLabels::useRosterHint());

    m_imageLabel = ui->imageLabel;

    m_targetLabel = ui->targetLabel;
    m_shotLabel = ui->shotLabel;
    m_shotProgress = ui->shotProgress;
    m_scoreLabel = ui->scoreLabel;
    QFont scoreFont = m_scoreLabel->font();
    scoreFont.setPointSize(scoreFont.pointSize() + 3);
    scoreFont.setBold(true);
    m_scoreLabel->setFont(scoreFont);
    m_syncLabel = ui->syncLabel;

    m_log = ui->log;

    m_startButton = ui->startButton;
    m_startButton->setText(VnLabels::startButton());
    m_stopButton = ui->stopButton;
    m_stopButton->setText(VnLabels::stopButton());
    // TESTING helper, deliberately separated from Start/Stop by a stretch so
    // it isn't mistaken for part of the normal run controls -- deletes this
    // lane's local db + images (see onResetClicked()).
    m_resetButton = ui->resetButton;
    m_resetButton->setText(VnLabels::resetButton());

    connect(m_startButton, &QPushButton::clicked, this, &MainWindow::onStartClicked);
    connect(m_stopButton, &QPushButton::clicked, this, &MainWindow::onStopClicked);
    connect(m_resetButton, &QPushButton::clicked, this, &MainWindow::onResetClicked);
    connect(m_addRosterBtn, &QPushButton::clicked, this, &MainWindow::onAddRosterRow);
    connect(m_removeRosterBtn, &QPushButton::clicked, this, &MainWindow::onRemoveRosterRow);
    connect(m_generateRosterBtn, &QPushButton::clicked, this, &MainWindow::onGenerateRoster);
    connect(m_laneSpinBox, QOverload<int>::of(&QSpinBox::valueChanged), this, &MainWindow::onLaneChanged);
}

void MainWindow::refreshInfoLabel()
{
    setWindowTitle(QStringLiteral("Lane %1 - Edge Simulator (Qt/C++)").arg(m_config.lane));
    m_infoLabel->setText(QStringLiteral("Server: %1 | Lane: %2 | Targets: %3 | Shots/target: %4")
                              .arg(m_config.server)
                              .arg(m_config.lane)
                              .arg(m_config.targets)
                              .arg(m_config.shots));
}

void MainWindow::onLaneChanged(int value)
{
    // Only reachable while nothing is running (the spinbox is disabled for
    // the duration of a run -- see onStartClicked()/onErrorOccurred()/
    // onAllDone()), so it's safe to just update m_config and the display;
    // the new lane number takes effect the next time Start seeds/opens a
    // session, exactly as if --lane had been passed with this value.
    m_config.lane = value;
    refreshInfoLabel();
}

void MainWindow::appendLog(const QString &text)
{
    if (m_log)
        m_log->appendPlainText(text);
}

void MainWindow::onStartClicked()
{
    if (m_worker && m_worker->isRunning())
        return;

    // A non-empty roster table takes precedence over --people/single-shooter
    // (same precedence EdgeWorker::run() applies), rebuilt fresh from the
    // table every Start so edits made between runs take effect.
    m_config.roster = collectRosterFromTable();

    m_startButton->setEnabled(false);
    m_stopButton->setEnabled(true);
    m_resetButton->setEnabled(false);
    m_rosterTable->setEnabled(false);
    m_addRosterBtn->setEnabled(false);
    m_removeRosterBtn->setEnabled(false);
    m_generateRosterBtn->setEnabled(false);
    m_laneSpinBox->setEnabled(false);
    m_imageLabel->setText(QStringLiteral("..."));
    appendLog(VnLabels::running());

    auto *worker = new EdgeWorker(m_config, this);
    m_worker = worker;

    connect(worker, &EdgeWorker::logMessage, this, &MainWindow::onLogMessage);
    connect(worker, &EdgeWorker::errorOccurred, this, &MainWindow::onErrorOccurred);
    connect(worker, &EdgeWorker::seeded, this, &MainWindow::onSeeded);
    connect(worker, &EdgeWorker::personStarted, this, &MainWindow::onPersonStarted);
    connect(worker, &EdgeWorker::rosterEntryStarted, this, &MainWindow::onRosterEntryStarted);
    connect(worker, &EdgeWorker::sessionStarted, this, &MainWindow::onSessionStarted);
    connect(worker, &EdgeWorker::shotFired, this, &MainWindow::onShotFired);
    connect(worker, &EdgeWorker::targetCompleted, this, &MainWindow::onTargetCompleted);
    connect(worker, &EdgeWorker::sessionCompleted, this, &MainWindow::onSessionCompleted);
    connect(worker, &EdgeWorker::syncResult, this, &MainWindow::onSyncResult);
    connect(worker, &EdgeWorker::allDone, this, &MainWindow::onAllDone);
    connect(worker, &QThread::finished, worker, &QObject::deleteLater);

    worker->start();
}

void MainWindow::onStopClicked()
{
    if (m_worker) {
        m_worker->requestStop();
        appendLog(VnLabels::stopButton() + QStringLiteral("..."));
    }
    m_stopButton->setEnabled(false);
}

void MainWindow::onSeeded(const QString &edgeUuid, const QString &edgeCode, const QString &dbPath)
{
    m_infoLabel->setText(QStringLiteral("Server: %1 | Lane: %2 | Edge: %3\nUUID: %4\nDB: %5")
                              .arg(m_config.server)
                              .arg(m_config.lane)
                              .arg(edgeCode, edgeUuid, dbPath));
}

void MainWindow::onPersonStarted(int index, int total, const QString &name, const QString &shooterCode)
{
    m_personLabel->setText(QStringLiteral("%1 %2/%3: %4 (%5)")
                                .arg(VnLabels::personWord())
                                .arg(index)
                                .arg(total)
                                .arg(name, shooterCode));
    m_personLabel->show();
}

void MainWindow::onRosterEntryStarted(int index, int total, const QString &name, const QString &shooterCode,
                                       const QString &unitCode, const QString &unitName)
{
    Q_UNUSED(unitCode);
    m_personLabel->setText(QStringLiteral("%1 %2/%3: %4 (%5)%6")
                                .arg(VnLabels::personWord())
                                .arg(index)
                                .arg(total)
                                .arg(name, shooterCode)
                                .arg(unitName.isEmpty() ? QString() : QStringLiteral(" / ") + unitName));
    m_personLabel->show();
}

void MainWindow::onSessionStarted(int sessionId, const QString &sessionUuid, int targetCount)
{
    Q_UNUSED(sessionUuid);
    m_targetLabel->setText(QStringLiteral("%1: -/%2").arg(VnLabels::targetWord()).arg(targetCount));
    m_shotLabel->setText(QStringLiteral("%1: -/%2").arg(VnLabels::shotWord()).arg(m_config.shots));
    m_shotProgress->setRange(0, m_config.shots);
    m_shotProgress->setValue(0);
    m_scoreLabel->clear();
    appendLog(QStringLiteral("Session #%1 started").arg(sessionId));
}

void MainWindow::onShotFired(int targetPosition, int shotNumber, int totalShots, double xMm, double yMm,
                              double score, const QByteArray &pngBytes)
{
    Q_UNUSED(xMm);
    Q_UNUSED(yMm);

    QPixmap pix;
    if (pix.loadFromData(pngBytes, "PNG"))
        m_imageLabel->setPixmap(pix.scaled(m_imageLabel->size(), Qt::KeepAspectRatio, Qt::SmoothTransformation));

    m_targetLabel->setText(QStringLiteral("%1: %2").arg(VnLabels::targetWord()).arg(targetPosition));
    m_shotLabel->setText(QStringLiteral("%1: %2/%3").arg(VnLabels::shotWord()).arg(shotNumber).arg(totalShots));
    m_shotProgress->setRange(0, totalShots);
    m_shotProgress->setValue(shotNumber);

    const QString band = VnLabels::classify(score);
    QString color;
    if (score >= 9.0)
        color = QStringLiteral("#0a8a0a");
    else if (score >= 7.0)
        color = QStringLiteral("#1565c0");
    else if (score >= 5.0)
        color = QStringLiteral("#e68a00");
    else
        color = QStringLiteral("#c62828");

    m_scoreLabel->setText(QStringLiteral("%1: %2 (%3)").arg(VnLabels::scoreWord()).arg(score, 0, 'f', 1).arg(band));
    m_scoreLabel->setStyleSheet(QStringLiteral("color:%1;").arg(color));
}

void MainWindow::onTargetCompleted(int targetPosition, int targetIndex, int targetCount)
{
    appendLog(QStringLiteral("%1 %2 (%3/%4) done").arg(VnLabels::targetWord()).arg(targetPosition).arg(targetIndex).arg(targetCount));
}

void MainWindow::onSessionCompleted(int sessionId)
{
    appendLog(QStringLiteral("Session #%1: %2").arg(sessionId).arg(VnLabels::done()));
}

void MainWindow::onSyncResult(bool ok, int accepted, int skipped, const QString &message)
{
    if (ok) {
        m_syncLabel->setText(QStringLiteral("%1 (%2)").arg(VnLabels::synced()).arg(accepted));
        m_syncLabel->setStyleSheet(QStringLiteral("color:#0a8a0a;"));
        Q_UNUSED(skipped);
    } else {
        m_syncLabel->setText(VnLabels::lanUnavailable());
        m_syncLabel->setStyleSheet(QStringLiteral("color:#c62828;"));
        Q_UNUSED(message);
    }
}

void MainWindow::onLogMessage(const QString &text)
{
    appendLog(text);
}

void MainWindow::onErrorOccurred(const QString &message)
{
    appendLog(QStringLiteral("[ERROR] %1").arg(message));
    m_startButton->setEnabled(true);
    m_stopButton->setEnabled(false);
    m_resetButton->setEnabled(true);
    m_rosterTable->setEnabled(true);
    m_addRosterBtn->setEnabled(true);
    m_removeRosterBtn->setEnabled(true);
    m_generateRosterBtn->setEnabled(true);
    m_laneSpinBox->setEnabled(true);
}

void MainWindow::onAllDone()
{
    m_startButton->setEnabled(true);
    m_stopButton->setEnabled(false);
    m_resetButton->setEnabled(true);
    m_rosterTable->setEnabled(true);
    m_addRosterBtn->setEnabled(true);
    m_removeRosterBtn->setEnabled(true);
    m_generateRosterBtn->setEnabled(true);
    m_laneSpinBox->setEnabled(true);
}

void MainWindow::onResetClicked()
{
    // Same resolution rule EdgeWorker::run() uses: an explicit --db path
    // wins, otherwise "edge<lane>.db" / "images_lane<lane>" next to the exe.
    const QString dbPath = m_config.dbPath.isEmpty()
        ? QCoreApplication::applicationDirPath() + QStringLiteral("/edge%1.db").arg(m_config.lane)
        : m_config.dbPath;
    const QString imagesDir = QCoreApplication::applicationDirPath() + QStringLiteral("/images_lane%1").arg(m_config.lane);

    const auto answer = QMessageBox::question(
        this, VnLabels::resetConfirmTitle(), VnLabels::resetConfirmText().arg(m_config.lane),
        QMessageBox::Yes | QMessageBox::No, QMessageBox::No);
    if (answer != QMessageBox::Yes)
        return;

    bool removedAnything = false;
    if (QFile::exists(dbPath)) {
        if (QFile::remove(dbPath))
            removedAnything = true;
        else
            appendLog(QStringLiteral("[ERROR] Could not remove %1").arg(dbPath));
    }
    QDir imgDir(imagesDir);
    if (imgDir.exists()) {
        if (imgDir.removeRecursively())
            removedAnything = true;
        else
            appendLog(QStringLiteral("[ERROR] Could not remove %1").arg(imagesDir));
    }
    appendLog(removedAnything ? VnLabels::resetDoneText().arg(m_config.lane)
                               : VnLabels::resetNothingText().arg(m_config.lane));
}

void MainWindow::addRosterRow(const QString &name, const QString &unitName, const QString &unitCode,
                               const QString &shooterCode)
{
    const int row = m_rosterTable->rowCount();
    m_rosterTable->insertRow(row);
    m_rosterTable->setItem(row, 0, new QTableWidgetItem(name));
    m_rosterTable->setItem(row, 1, new QTableWidgetItem(unitName));
    m_rosterTable->setItem(row, 2, new QTableWidgetItem(unitCode));
    m_rosterTable->setItem(row, 3, new QTableWidgetItem(shooterCode));
}

void MainWindow::onAddRosterRow()
{
    addRosterRow();
    m_rosterTable->setFocus();
    m_rosterTable->setCurrentCell(m_rosterTable->rowCount() - 1, 0);
}

void MainWindow::onRemoveRosterRow()
{
    const QList<QTableWidgetItem *> sel = m_rosterTable->selectedItems();
    if (sel.isEmpty()) {
        if (m_rosterTable->rowCount() > 0)
            m_rosterTable->removeRow(m_rosterTable->rowCount() - 1);
        return;
    }
    QSet<int> rows;
    for (QTableWidgetItem *item : sel)
        rows.insert(item->row());
    QList<int> sorted = rows.values();
    std::sort(sorted.begin(), sorted.end(), std::greater<int>());
    for (int r : sorted)
        m_rosterTable->removeRow(r);
}

void MainWindow::onGenerateRoster()
{
    // Round-robin N generated shooters across M generated units (e.g. 5
    // shooters / 2 units -> unit 1,2,1,2,1), each with the usual VN name
    // pool and an auto-numbered unit/shooter code -- the "generate
    // automatically" button. Appends to whatever rows already exist rather
    // than replacing them, so a manually-added shooter is never lost.
    //
    // Every shooter's position is offset by m_rosterGenSeq (how many this
    // button has already generated in this run), NOT restarted at 0 each
    // click -- otherwise a second click would reissue the exact same
    // GEN-001, GEN-002, ... shooter codes as the first click (which,
    // combined with the same round-robin unit codes, means the SAME
    // shooter identity server-side -- see schema.sql's
    // UNIQUE(unit_id, shooter_code) -- so "Generate" twice would silently
    // merge into the first batch instead of adding new people) and would
    // always start back at the first name in the pool instead of
    // continuing through it.
    const int shooterCount = m_genShooterCount->value();
    const int unitCount = m_genUnitCount->value();
    const QStringList &pool = vnShooterNames();
    for (int k = 0; k < shooterCount; ++k) {
        const int i = m_rosterGenSeq + k;
        const QString name = pool.at(i % pool.size());
        const int unitIdx = (i % unitCount) + 1;
        const QString unitName = QStringLiteral("%1 %2").arg(VnLabels::genUnitPrefix()).arg(unitIdx);
        const QString unitCode = QStringLiteral("UNIT-GEN-%1").arg(unitIdx);
        const QString shooterCode = QStringLiteral("GEN-%1").arg(i + 1, 3, 10, QChar('0'));
        addRosterRow(name, unitName, unitCode, shooterCode);
    }
    m_rosterGenSeq += shooterCount;
}

QVector<RosterEntry> MainWindow::collectRosterFromTable() const
{
    QVector<RosterEntry> roster;
    const int rows = m_rosterTable->rowCount();
    for (int r = 0; r < rows; ++r) {
        auto text = [this, r](int col) {
            QTableWidgetItem *it = m_rosterTable->item(r, col);
            return it ? it->text().trimmed() : QString();
        };
        const QString name = text(0);
        if (name.isEmpty())
            continue; // a blank name means an empty/placeholder row -- skip it
        RosterEntry entry;
        entry.name = name;
        entry.unitName = text(1);
        entry.unitCode = text(2);
        entry.shooterCode = text(3);
        roster.append(entry);
    }
    return roster;
}
