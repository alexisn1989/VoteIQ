-- Seed: congress_missed_nominations and congress_missed_bills
-- 13 nominations + 16 bills for VA federal delegation (119th Congress)

CREATE TABLE IF NOT EXISTS congress_missed_nominations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bioguide_id TEXT NOT NULL,
    congress    INTEGER,
    citation    TEXT,
    nominee_name TEXT,
    position    TEXT,
    vote_date   TEXT,
    yea_count   INTEGER,
    nay_count   INTEGER,
    result      TEXT,
    margin      INTEGER,
    note        TEXT
);

CREATE TABLE IF NOT EXISTS congress_missed_bills (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    bioguide_id   TEXT NOT NULL,
    congress      INTEGER,
    bill          TEXT,
    question_type TEXT,
    vote_date     TEXT,
    bill_title    TEXT,
    yea_count     INTEGER,
    nay_count     INTEGER,
    margin        INTEGER,
    result        TEXT,
    became_law    INTEGER DEFAULT 0,
    note          TEXT
);

INSERT OR IGNORE INTO congress_missed_nominations (bioguide_id, congress, citation, nominee_name, position, vote_date, yea_count, nay_count, result, margin, note) VALUES ('W000805', 119, 'PN12-25', 'Christopher Landau', 'Deputy Secretary of State', '2025-03-24', 60, 31, 'Confirmed', 29, NULL);
INSERT OR IGNORE INTO congress_missed_nominations (bioguide_id, congress, citation, nominee_name, position, vote_date, yea_count, nay_count, result, margin, note) VALUES ('W000805', 119, 'PN12-15', 'Michael Faulkender', 'Deputy Secretary of the Treasury', '2025-03-26', 53, 43, 'Confirmed', 10, NULL);
INSERT OR IGNORE INTO congress_missed_nominations (bioguide_id, congress, citation, nominee_name, position, vote_date, yea_count, nay_count, result, margin, note) VALUES ('W000805', 119, 'PN12-18', 'Paul Atkins', 'Member / Chair, Securities and Exchange Commission (Virginia)', '2025-04-09', 52, 44, 'Confirmed', 8, 'Virginia-based nominee; Warner is Virginia senior senator');
INSERT OR IGNORE INTO congress_missed_nominations (bioguide_id, congress, citation, nominee_name, position, vote_date, yea_count, nay_count, result, margin, note) VALUES ('W000805', 119, 'PN13-6', 'David Fotouhi', 'Deputy Administrator, Environmental Protection Agency', '2026-05-07', 53, 41, 'Confirmed', 12, NULL);
INSERT OR IGNORE INTO congress_missed_nominations (bioguide_id, congress, citation, nominee_name, position, vote_date, yea_count, nay_count, result, margin, note) VALUES ('W000805', 119, 'PN368', 'Stephen Chad Meredith', 'US District Judge, Eastern District of Kentucky', '2025-10-23', 48, 45, 'Confirmed', 3, 'Lifetime judicial appointment, close 48-45 vote');
INSERT OR IGNORE INTO congress_missed_nominations (bioguide_id, congress, citation, nominee_name, position, vote_date, yea_count, nay_count, result, margin, note) VALUES ('W000805', 119, 'PN787-1', 'Andrew B. Davis', 'US District Judge, Western District of Texas', '2026-04-20', 47, 46, 'Confirmed', 1, 'Lifetime judicial appointment confirmed by single vote (47-46)');
INSERT OR IGNORE INTO congress_missed_nominations (bioguide_id, congress, citation, nominee_name, position, vote_date, yea_count, nay_count, result, margin, note) VALUES ('K000384', 119, 'PN368', 'Stephen Chad Meredith', 'US District Judge, Eastern District of Kentucky', '2025-10-23', 48, 45, 'Confirmed', 3, 'Lifetime judicial appointment confirmed 48-45; both VA senators absent');
INSERT OR IGNORE INTO congress_missed_nominations (bioguide_id, congress, citation, nominee_name, position, vote_date, yea_count, nay_count, result, margin, note) VALUES ('K000384', 119, 'PN11-17', 'Jamieson Greer', 'US Trade Representative (cloture vote)', '2025-02-24', 56, 43, 'Confirmed', 13, 'Trump trade czar; Kaine missed cloture vote');
INSERT OR IGNORE INTO congress_missed_nominations (bioguide_id, congress, citation, nominee_name, position, vote_date, yea_count, nay_count, result, margin, note) VALUES ('K000384', 119, 'PN54-5', 'David Perdue', 'Ambassador to China (cloture vote)', '2025-04-28', 67, 29, 'Confirmed', 38, NULL);
INSERT OR IGNORE INTO congress_missed_nominations (bioguide_id, congress, citation, nominee_name, position, vote_date, yea_count, nay_count, result, margin, note) VALUES ('K000384', 119, 'PN24-3', 'John Hurley', 'Under Secretary of Treasury for Terrorism & Financial Crimes (cloture vote)', '2025-07-17', 51, 47, 'Confirmed', 4, 'Very close 51-47 cloture vote');
INSERT OR IGNORE INTO congress_missed_nominations (bioguide_id, congress, citation, nominee_name, position, vote_date, yea_count, nay_count, result, margin, note) VALUES ('K000384', 119, 'PN22-25', 'Arielle Roth', 'Assistant Secretary of Commerce/NTIA (cloture vote)', '2026-03-22', 52, 41, 'Confirmed', 11, NULL);
INSERT OR IGNORE INTO congress_missed_nominations (bioguide_id, congress, citation, nominee_name, position, vote_date, yea_count, nay_count, result, margin, note) VALUES ('K000384', 119, 'PN12-12', 'Daniel Driscoll', 'Secretary of the Army (cloture vote)', '2026-04-22', 66, 28, 'Confirmed', 38, NULL);
INSERT OR IGNORE INTO congress_missed_nominations (bioguide_id, congress, citation, nominee_name, position, vote_date, yea_count, nay_count, result, margin, note) VALUES ('K000384', 119, 'PN858', 'Markwayne Mullin', 'Secretary of Homeland Security (cloture vote)', '2026-03-20', 54, 45, 'Confirmed', 9, 'Major cabinet position — DHS Secretary');

INSERT OR IGNORE INTO congress_missed_bills (bioguide_id, congress, bill, question_type, vote_date, bill_title, yea_count, nay_count, margin, result, became_law, note) VALUES ('W000804', 119, 'H J RES 61', 'On Passage', '2025-03-05', 'Congressional Review Act disapproval (regulatory rollback)', 216, 202, 14, 'Passed', 1, 'Became Public Law 119-14');
INSERT OR IGNORE INTO congress_missed_bills (bioguide_id, congress, bill, question_type, vote_date, bill_title, yea_count, nay_count, margin, result, became_law, note) VALUES ('W000804', 119, 'H R 2243', 'On Passage', '2025-05-14', 'LEOSA Reform Act (law enforcement officer firearms)', 229, 193, 36, 'Passed', 0, NULL);
INSERT OR IGNORE INTO congress_missed_bills (bioguide_id, congress, bill, question_type, vote_date, bill_title, yea_count, nay_count, margin, result, became_law, note) VALUES ('W000804', 119, 'H R 3944', 'On Agreeing to the Amendment', '2025-06-25', 'Military Construction & Veterans Affairs Appropriations Act (amendment)', 219, 206, 13, 'Agreed to', 0, 'Close 219-206; Wittman sits on Armed Services Committee');
INSERT OR IGNORE INTO congress_missed_bills (bioguide_id, congress, bill, question_type, vote_date, bill_title, yea_count, nay_count, margin, result, became_law, note) VALUES ('W000804', 119, 'H R 3838', 'On Agreeing to the Amendment', '2025-09-10', 'SPEED & National Defense Authorization Act (NDAA amendment)', 227, 201, 26, 'Agreed to', 0, 'NDAA amendment; Wittman sits on Armed Services Committee');
INSERT OR IGNORE INTO congress_missed_bills (bioguide_id, congress, bill, question_type, vote_date, bill_title, yea_count, nay_count, margin, result, became_law, note) VALUES ('W000804', 119, 'H R 4690', 'On Passage', '2026-04-22', 'Reliable Federal Infrastructure Act', 214, 202, 12, 'Passed', 0, 'Close 214-202');
INSERT OR IGNORE INTO congress_missed_bills (bioguide_id, congress, bill, question_type, vote_date, bill_title, yea_count, nay_count, margin, result, became_law, note) VALUES ('K000399', 119, 'H J RES 24', 'On Passage', '2025-03-27', 'Congressional Review Act disapproval (regulatory rollback)', 203, 182, 21, 'Passed', 1, 'Became Public Law 119-7');
INSERT OR IGNORE INTO congress_missed_bills (bioguide_id, congress, bill, question_type, vote_date, bill_title, yea_count, nay_count, margin, result, became_law, note) VALUES ('W000805', 119, 'S.Amdt. 2862', 'On the Amendment', '2025-07-16', 'Schiff Amendment No. 2862 to H.R. 4', 46, 51, -5, 'Rejected', 0, 'Democratic amendment; Warner absent');
INSERT OR IGNORE INTO congress_missed_bills (bioguide_id, congress, bill, question_type, vote_date, bill_title, yea_count, nay_count, margin, result, became_law, note) VALUES ('W000805', 119, 'S.Amdt. 5281', 'On the Amendment', '2026-04-22', 'Graham Amendment No. 5281 to S.Con.Res. 33 (Big Beautiful Bill vote-a-rama)', 98, 0, 98, 'Agreed to', 0, 'Unanimous 98-0; Warner absent');
INSERT OR IGNORE INTO congress_missed_bills (bioguide_id, congress, bill, question_type, vote_date, bill_title, yea_count, nay_count, margin, result, became_law, note) VALUES ('W000805', 119, 'S.Amdt. 5378', 'On the Amendment', '2026-04-23', 'Paul Amendment No. 5378 to S.Con.Res. 33 (Big Beautiful Bill vote-a-rama)', 25, 73, -48, 'Rejected', 0, NULL);
INSERT OR IGNORE INTO congress_missed_bills (bioguide_id, congress, bill, question_type, vote_date, bill_title, yea_count, nay_count, margin, result, became_law, note) VALUES ('W000805', 119, 'S.Amdt. 5235', 'On the Amendment', '2026-04-23', 'Merkley Amendment No. 5235 to S.Con.Res. 33 (Big Beautiful Bill vote-a-rama)', 46, 52, -6, 'Rejected', 0, NULL);
INSERT OR IGNORE INTO congress_missed_bills (bioguide_id, congress, bill, question_type, vote_date, bill_title, yea_count, nay_count, margin, result, became_law, note) VALUES ('W000805', 119, 'S.Amdt. 5336', 'On the Amendment', '2026-04-23', 'Wyden Amendment No. 5336 to S.Con.Res. 33 (Big Beautiful Bill vote-a-rama)', 48, 50, -2, 'Rejected', 0, 'Close 48-50; Warner absent');
INSERT OR IGNORE INTO congress_missed_bills (bioguide_id, congress, bill, question_type, vote_date, bill_title, yea_count, nay_count, margin, result, became_law, note) VALUES ('W000805', 119, 'S.Amdt. 5333', 'On the Amendment', '2026-04-23', 'Schiff Amendment No. 5333 to S.Con.Res. 33 (Big Beautiful Bill vote-a-rama)', 49, 49, 0, 'Rejected', 0, 'TIED 49-49; Warner absence cost Democrats this vote');
INSERT OR IGNORE INTO congress_missed_bills (bioguide_id, congress, bill, question_type, vote_date, bill_title, yea_count, nay_count, margin, result, became_law, note) VALUES ('W000805', 119, 'S.Con.Res. 33', 'On the Concurrent Resolution', '2026-04-23', 'Big Beautiful Bill Budget Resolution (S.Con.Res. 33)', 50, 48, 2, 'Agreed to', 0, 'Budget resolution passed 50-48; Warner absent');
INSERT OR IGNORE INTO congress_missed_bills (bioguide_id, congress, bill, question_type, vote_date, bill_title, yea_count, nay_count, margin, result, became_law, note) VALUES ('W000831', 119, 'H R 3383', 'On Agreeing to the Amendment', '2025-12-11', 'INVEST Capital Formation Act (amendment)', 54, 374, -320, 'Failed', 0, 'Amendment failed 54-374; low newsworthiness');
INSERT OR IGNORE INTO congress_missed_bills (bioguide_id, congress, bill, question_type, vote_date, bill_title, yea_count, nay_count, margin, result, became_law, note) VALUES ('G000568', 119, 'H R 7006', 'On Agreeing to the Amendment', '2026-01-14', 'Financial Services & National Security Appropriations Act (amendment)', 163, 257, -94, 'Failed', 0, 'Amendment failed 163-257');
INSERT OR IGNORE INTO congress_missed_bills (bioguide_id, congress, bill, question_type, vote_date, bill_title, yea_count, nay_count, margin, result, became_law, note) VALUES ('M001239', 119, 'H R 7148', 'On Agreeing to the Amendment', '2026-01-22', 'Consolidated Appropriations Act 2026 (amendment)', 136, 291, -155, 'Failed', 1, 'Appropriations bill became P.L. 119-75; amendment itself failed 136-291');
