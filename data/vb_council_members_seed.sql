-- Virginia Beach City Council member roster seed (11 members, scraped 2026-06-22)
-- Idempotent: CREATE TABLE IF NOT EXISTS + INSERT OR IGNORE

CREATE TABLE IF NOT EXISTS vb_council_members (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    district      TEXT NOT NULL,
    district_num  INTEGER NOT NULL,
    email         TEXT,
    jurisdiction  TEXT DEFAULT 'Virginia Beach',
    scraped_date  TEXT
);

INSERT OR IGNORE INTO vb_council_members (name, district, district_num, email, jurisdiction, scraped_date) VALUES
    ('Robert M. "Bobby" Dyer', 'Mayor',      0,  'mayorsoffice@vbgov.com', 'Virginia Beach', '2026-06-22'),
    ('David Hutcheson',         'District 1', 1,  'dhutcheson@vbgov.com',   'Virginia Beach', '2026-06-22'),
    ('Barbara Henley',          'District 2', 2,  'bhenley@vbgov.com',      'Virginia Beach', '2026-06-22'),
    ('Michael Berlucchi',       'District 3', 3,  'mberlucc@vbgov.com',     'Virginia Beach', '2026-06-22'),
    ('Dr. Amelia Ross-Hammond', 'District 4', 4,  'arosshammond@vbgov.com', 'Virginia Beach', '2026-06-22'),
    ('Rosemary Wilson',         'District 5', 5,  'rcwilson@vbgov.com',     'Virginia Beach', '2026-06-22'),
    ('Robert W. "Worth" Remick','District 6', 6,  'wremick@vbgov.com',      'Virginia Beach', '2026-06-22'),
    ('Cal "Cash" Jackson-Green','District 7', 7,  'cjacksongreen@vbgov.com','Virginia Beach', '2026-06-22'),
    ('Stacy Cummings',          'District 8', 8,  'stcummings@vbgov.com',   'Virginia Beach', '2026-06-22'),
    ('Joashua F. Schulman',     'District 9', 9,  'jschulman@vbgov.com',    'Virginia Beach', '2026-06-22'),
    ('Jennifer V. Rouse',       'District 10',10, 'jvrouse@vbgov.com',      'Virginia Beach', '2026-06-22');
