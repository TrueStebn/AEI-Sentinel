-- AEI Sentinel - SQLite database schema v1.0
-- Source: JunctionX Turku 2026, Tyks Laboratories x Mylab synthetic dataset.
-- The database uses one .db file on the application host.
-- Enable foreign keys on EVERY SQLite connection; journal_mode and synchronous
-- are connection-level initialization settings, not substitutes for backups.
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = FULL;
PRAGMA busy_timeout = 5000;

BEGIN IMMEDIATE;

-- =============================
-- 1. LABORATORY SOURCE DATA
-- =============================
CREATE TABLE IF NOT EXISTS patients (
    patient_id TEXT PRIMARY KEY,
    sex TEXT NOT NULL CHECK (sex IN ('F', 'M')),
    birth_year INTEGER NOT NULL,
    home_area TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wards (
    ward_code TEXT PRIMARY KEY,
    ward_fi TEXT NOT NULL,
    ward_en TEXT NOT NULL,
    ward_type TEXT NOT NULL CHECK (ward_type IN ('in', 'out')),
    institution TEXT NOT NULL CHECK (institution IN ('hospital', 'primary_care'))
);

CREATE TABLE IF NOT EXISTS antibiotics (
    antibiotic_code TEXT PRIMARY KEY,
    antibiotic TEXT NOT NULL UNIQUE,
    antibiotic_fi TEXT NOT NULL,
    antibiotic_class TEXT NOT NULL,
    reported_for TEXT NOT NULL CHECK (reported_for IN ('all', 'urine'))
);

CREATE TABLE IF NOT EXISTS cultures (
    culture_id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(patient_id),
    age_years INTEGER NOT NULL CHECK (age_years BETWEEN 0 AND 130),
    sample_date TEXT NOT NULL, -- YYYY-MM-DD, UTC-independent calendar date
    iso_year INTEGER NOT NULL,
    iso_week INTEGER NOT NULL CHECK (iso_week BETWEEN 1 AND 53),
    ward_code TEXT NOT NULL REFERENCES wards(ward_code),
    admission_date TEXT, -- NULL for outpatients
    specimen TEXT NOT NULL CHECK (specimen IN ('urine', 'blood', 'wound')),
    specimen_fi TEXT NOT NULL,
    result TEXT NOT NULL CHECK (result IN ('positive', 'negative', 'mixed_growth')),
    n_isolates_declared INTEGER NOT NULL CHECK (n_isolates_declared BETWEEN 0 AND 2)
);

CREATE TABLE IF NOT EXISTS isolates (
    isolate_id TEXT PRIMARY KEY,
    culture_id TEXT NOT NULL REFERENCES cultures(culture_id),
    isolate_no INTEGER NOT NULL CHECK (isolate_no BETWEEN 1 AND 2),
    organism TEXT NOT NULL,
    quantity TEXT, -- original source text such as ">=10^5" or "+++"
    esbl INTEGER NOT NULL CHECK (esbl IN (0, 1)),
    mrsa INTEGER NOT NULL CHECK (mrsa IN (0, 1)),
    vre INTEGER NOT NULL CHECK (vre IN (0, 1)),
    cpe INTEGER NOT NULL CHECK (cpe IN (0, 1)),
    UNIQUE (culture_id, isolate_no)
);

-- One test result per isolate and antibiotic. CSV duplicates culture_id,
-- organism and antibiotic labels; the importer validates these against the
-- parent tables but does not store the redundant columns here.
CREATE TABLE IF NOT EXISTS susceptibility (
    isolate_id TEXT NOT NULL REFERENCES isolates(isolate_id),
    antibiotic_code TEXT NOT NULL REFERENCES antibiotics(antibiotic_code),
    result TEXT NOT NULL CHECK (result IN ('S', 'R')),
    guideline TEXT NOT NULL,
    PRIMARY KEY (isolate_id, antibiotic_code)
);

-- =============================
-- 2. INGESTION AND COMPUTATIONS
-- =============================
-- Singleton; increment ONLY after a successful import changes source data.
CREATE TABLE IF NOT EXISTS data_version (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    version INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT OR IGNORE INTO data_version(singleton_id, version) VALUES (1, 0);

CREATE TABLE IF NOT EXISTS import_batches (
    batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
    origin TEXT NOT NULL, -- initial_zip, csv_upload, or simulator
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    rows_inserted INTEGER NOT NULL DEFAULT 0 CHECK (rows_inserted >= 0),
    rows_unchanged INTEGER NOT NULL DEFAULT 0 CHECK (rows_unchanged >= 0),
    error_message TEXT,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS import_files (
    file_id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES import_batches(batch_id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    row_count INTEGER NOT NULL DEFAULT 0 CHECK (row_count >= 0),
    UNIQUE (batch_id, filename)
);

-- key: sha256(canonical sorted filters + window boundaries + analysis settings)
-- Stale results are ignored if data_version or algorithm_version mismatches.
CREATE TABLE IF NOT EXISTS analysis_cache (
    cache_key TEXT PRIMARY KEY,
    data_revision INTEGER NOT NULL CHECK (data_revision >= 0),
    algorithm_version TEXT NOT NULL,
    filters_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    computed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- =============================
-- 3. USER WORKSPACES AND RECOVERY
-- =============================
-- For hackathon, user_key='demo'. Real multi-user use requires authentication,
-- separate access control and a suitable production database/security design.
CREATE TABLE IF NOT EXISTS user_sessions (
    session_id TEXT PRIMARY KEY, -- generated UUID
    user_key TEXT NOT NULL DEFAULT 'demo',
    page_name TEXT NOT NULL DEFAULT 'Dashboard',
    state_json TEXT NOT NULL DEFAULT '{}', -- filters, selected dates, draft notes
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_saved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Explicit, named saved copies of a workspace (not a replacement for autosave).
CREATE TABLE IF NOT EXISTS saved_workspaces (
    workspace_id TEXT PRIMARY KEY, -- generated UUID
    owner_key TEXT NOT NULL,
    session_id TEXT REFERENCES user_sessions(session_id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    state_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- =============================
-- 4. LOOKUP INDEXES
-- =============================
CREATE INDEX IF NOT EXISTS idx_cultures_date
    ON cultures(sample_date);
CREATE INDEX IF NOT EXISTS idx_cultures_ward_date
    ON cultures(ward_code, sample_date);
CREATE INDEX IF NOT EXISTS idx_cultures_patient_date
    ON cultures(patient_id, sample_date);
CREATE INDEX IF NOT EXISTS idx_cultures_specimen_date
    ON cultures(specimen, sample_date);
CREATE INDEX IF NOT EXISTS idx_isolates_organism_culture
    ON isolates(organism, culture_id);
CREATE INDEX IF NOT EXISTS idx_isolates_culture
    ON isolates(culture_id);
CREATE INDEX IF NOT EXISTS idx_susceptibility_antibiotic_result
    ON susceptibility(antibiotic_code, result);
CREATE INDEX IF NOT EXISTS idx_import_files_hash
    ON import_files(sha256);
CREATE INDEX IF NOT EXISTS idx_analysis_cache_revision
    ON analysis_cache(data_revision, algorithm_version);
CREATE INDEX IF NOT EXISTS idx_user_sessions_recent
    ON user_sessions(user_key, last_saved_at DESC);
CREATE INDEX IF NOT EXISTS idx_saved_workspaces_owner
    ON saved_workspaces(owner_key, updated_at DESC);

PRAGMA user_version = 1;
COMMIT;
