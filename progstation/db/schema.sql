-- Schema for the RPi Sensor Programming Station (SRS section 11 / Appendix A).
-- Every table is additive; migrations live in database.py and bump user_version.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS Users (
    UserId          INTEGER PRIMARY KEY AUTOINCREMENT,
    Username        TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    FullName        TEXT    NOT NULL DEFAULT '',
    Role            TEXT    NOT NULL CHECK (Role IN ('operator', 'admin')),
    PasswordHash    TEXT    NOT NULL,
    Active          INTEGER NOT NULL DEFAULT 1,
    MustChangePw    INTEGER NOT NULL DEFAULT 0,
    FailedLogins    INTEGER NOT NULL DEFAULT 0,
    LockedUntil     TEXT,
    CreatedAt       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    LastLoginAt     TEXT
);

CREATE TABLE IF NOT EXISTS Projects (
    ProjectId       INTEGER PRIMARY KEY AUTOINCREMENT,
    ProjectName     TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    MCU             TEXT    NOT NULL,
    HexPath         TEXT    NOT NULL,
    -- Legacy flat layout (Appendix A).  Used when EepromMap is NULL.
    SNAddress       INTEGER NOT NULL DEFAULT 0,
    MFGAddress      INTEGER NOT NULL DEFAULT 8,
    -- Full configurable layout for products that need more than the two
    -- legacy addresses; JSON, see core/eeprom.py.
    EepromMap       TEXT,
    HwRevision      TEXT    NOT NULL DEFAULT '',
    ProductVariant  TEXT    NOT NULL DEFAULT '',
    FirmwareVersion TEXT    NOT NULL DEFAULT '',
    SerialPrefix    TEXT    NOT NULL DEFAULT '',
    SerialStart     INTEGER NOT NULL DEFAULT 1,
    SerialEnd       INTEGER NOT NULL DEFAULT 999999,
    SerialDigits    INTEGER NOT NULL DEFAULT 6,
    Signature       TEXT    NOT NULL DEFAULT '',
    FuseLow         TEXT,
    FuseHigh        TEXT,
    FuseExtended    TEXT,
    LockByte        TEXT,
    Active          INTEGER NOT NULL DEFAULT 1,
    CreatedAt       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UpdatedAt       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS SerialCounters (
    ProjectId       INTEGER PRIMARY KEY REFERENCES Projects(ProjectId) ON DELETE CASCADE,
    NextSerial      INTEGER NOT NULL,
    LastIssued      INTEGER,
    UpdatedAt       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS ProductionLog (
    LogId           INTEGER PRIMARY KEY AUTOINCREMENT,
    Timestamp       TEXT    NOT NULL,
    StationId       TEXT    NOT NULL DEFAULT '',
    ProjectId       INTEGER REFERENCES Projects(ProjectId) ON DELETE SET NULL,
    ProjectName     TEXT    NOT NULL DEFAULT '',
    SerialNo        TEXT,
    SerialValue     INTEGER,
    Result          TEXT    NOT NULL CHECK (Result IN ('PASS', 'FAIL')),
    Operator        TEXT    NOT NULL DEFAULT '',
    FirmwareVersion TEXT    NOT NULL DEFAULT '',
    HwRevision      TEXT    NOT NULL DEFAULT '',
    ProductVariant  TEXT    NOT NULL DEFAULT '',
    Signature       TEXT    NOT NULL DEFAULT '',
    DurationMs      INTEGER NOT NULL DEFAULT 0,
    ErrorCode       TEXT    NOT NULL DEFAULT '',
    Error           TEXT    NOT NULL DEFAULT '',
    EepromHex       TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_log_timestamp ON ProductionLog(Timestamp);
CREATE INDEX IF NOT EXISTS ix_log_serial    ON ProductionLog(SerialNo);
CREATE INDEX IF NOT EXISTS ix_log_operator  ON ProductionLog(Operator);
CREATE INDEX IF NOT EXISTS ix_log_result    ON ProductionLog(Result);
CREATE INDEX IF NOT EXISTS ix_log_project   ON ProductionLog(ProjectId, Timestamp);
CREATE INDEX IF NOT EXISTS ix_log_fw        ON ProductionLog(FirmwareVersion);

-- Security / traceability trail (SRS section 17).
CREATE TABLE IF NOT EXISTS AuditLog (
    AuditId         INTEGER PRIMARY KEY AUTOINCREMENT,
    Timestamp       TEXT    NOT NULL,
    Username        TEXT    NOT NULL DEFAULT '',
    Action          TEXT    NOT NULL,
    Target          TEXT    NOT NULL DEFAULT '',
    Detail          TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_audit_timestamp ON AuditLog(Timestamp);

-- Backup bookkeeping (SRS section 15/16).
CREATE TABLE IF NOT EXISTS BackupLog (
    BackupId        INTEGER PRIMARY KEY AUTOINCREMENT,
    Timestamp       TEXT    NOT NULL,
    Destination     TEXT    NOT NULL DEFAULT '',
    Result          TEXT    NOT NULL CHECK (Result IN ('PASS', 'FAIL')),
    Bytes           INTEGER NOT NULL DEFAULT 0,
    Detail          TEXT    NOT NULL DEFAULT ''
);
