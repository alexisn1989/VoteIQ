-- fix_cf_name_mappings.sql
-- Populates cf_table_name in va_name_index for 85 legislators whose
-- canonical_name already exists but cf_table_name was NULL.
-- cf_table_name must match the exact candidate_name string in va_cf_schedule_a.
--
-- Verified by fuzzy last-name search against va_cf_schedule_a cycles 2021-2025.
-- Run against polls.db (local: project root; production: /var/data/polls.db).
-- Safe to re-run — UPDATE WHERE cf_table_name IS NULL won't overwrite existing mappings.

-- ── Single-match (unambiguous) ────────────────────────────────────────────────

UPDATE va_name_index SET cf_table_name = 'Hon. Adam  Ebbin'
    WHERE canonical_name = 'Adam Ebbin'          AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Amanda Etter Batten'
    WHERE canonical_name = 'Amanda Batten'        AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Angelia Williams Graves'
    WHERE canonical_name = 'Angelia Graves'       AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Mr William Robert DeSteph Jr'
    WHERE canonical_name = 'Bill DeSteph'         AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Ms. Carrie Emerson Coyner'
    WHERE canonical_name = 'Carrie Coyner'        AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Mr. Christopher S. Runion'
    WHERE canonical_name = 'Chris Runion'         AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Mr. David Robert Suetterlein'
    WHERE canonical_name = 'David Suetterlein'    AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Delegate Delores  McQuinn'
    WHERE canonical_name = 'Delores McQuinn'      AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Mr Eric Robert Zehr'
    WHERE canonical_name = 'Eric Zehr'            AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Geary Michael Higgins'
    WHERE canonical_name = 'Geary Higgins'        AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Mrs Holly Melissa Seibold'
    WHERE canonical_name = 'Holly Seibold'        AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Mr. Israel D. O''Quinn'
    WHERE canonical_name = 'Israel O''Quinn'      AND cf_table_name IS NULL;
-- NOTE: CF data has embedded double-quotes in this name (data quality issue in source)
UPDATE va_name_index SET cf_table_name = 'James Jay" A. Leftwich Jr."'
    WHERE canonical_name = 'Jay Leftwich'         AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Laura Jane Hoffman Cohen'
    WHERE canonical_name = 'Laura Jane Cohen'     AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Delegate Luke Edward Torian'
    WHERE canonical_name = 'Luke Torian'          AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Luther Henry Cifers III'
    WHERE canonical_name = 'Luther Cifers'        AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Mark L. Earley Jr.'
    WHERE canonical_name = 'Mark Earley'          AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Mr. Mark Joseph Peake'
    WHERE canonical_name = 'Mark Peake'           AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Hon. Mark David Sickles'
    WHERE canonical_name = 'Mark Sickles'         AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Ms. May  Nivar'
    WHERE canonical_name = 'May Nivar'            AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Michael Benjamin Feggans'
    WHERE canonical_name = 'Michael Feggans'      AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Mr. Nicholas Jason Freitas'
    WHERE canonical_name = 'Nick Freitas'         AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Mr. Paul Eugene Krizek'
    WHERE canonical_name = 'Paul Krizek'          AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Rae Catrice Cousins'
    WHERE canonical_name = 'Rae Cousins'          AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Mr Robert Spurgeon Bloxom jr'
    WHERE canonical_name = 'Rob Bloxom'           AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Mr. Rodney T. Willett'
    WHERE canonical_name = 'Rod Willett'          AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Senator Ryan Todd McDougle'
    WHERE canonical_name = 'Ryan McDougle'        AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Mr Saddam Azlan Salim'
    WHERE canonical_name = 'Saddam Salim'         AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Tara Anne Durant'
    WHERE canonical_name = 'Tara Durant'          AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Dr. Todd E. Pillion'
    WHERE canonical_name = 'Todd Pillion'         AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Mr. James William Morefield'
    WHERE canonical_name = 'Will Morefield'       AND cf_table_name IS NULL;

-- ── Multi-match resolved (high-confidence) ───────────────────────────────────

UPDATE va_name_index SET cf_table_name = 'Mr. Aijalon Carlton Cordoza'
    WHERE canonical_name = 'A.C. Cordoza'         AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Alex Quentin Askew'
    WHERE canonical_name = 'Alex Askew'           AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Betsy Brooks Carr'
    WHERE canonical_name = 'Betsy Carr'           AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'William (Bill) M Stanley'
    WHERE canonical_name = 'Bill Stanley'         AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Mr. Bill  Wiley'
    WHERE canonical_name = 'Bill Wiley'           AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Robert D. Orrock Sr'
    WHERE canonical_name = 'Bobby Orrock'         AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Bonita Grace Anthony'
    WHERE canonical_name = 'Bonita Anthony'       AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Mr Hyland Franklin Fowler Jr'
    WHERE canonical_name = 'Buddy Fowler'         AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Mrs. Candi  King'
    WHERE canonical_name = 'Candi King'           AND cf_table_name IS NULL;
-- NOTE: two CF versions exist (William Chad Green / Mr. William Chadwick Green);
-- using formal legal name — verify donations aren't split across both spellings
UPDATE va_name_index SET cf_table_name = 'Mr. William Chadwick Green'
    WHERE canonical_name = 'Chad Green'           AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Ms. Charniele Lerhonda Herring'
    WHERE canonical_name = 'Charniele Herring'    AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Delegate Christopher Tracy Head'
    WHERE canonical_name = 'Chris Head'           AND cf_table_name IS NULL;
-- Chris Obenshain's legal first name is Joseph Christian "Chris" Obenshain
UPDATE va_name_index SET cf_table_name = 'Mr. Joseph Christian Obenshain'
    WHERE canonical_name = 'Chris Obenshain'      AND cf_table_name IS NULL;
-- Cia Price files as Marcia S Price
UPDATE va_name_index SET cf_table_name = 'Marcia S Price'
    WHERE canonical_name = 'Cia Price'            AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Mr. Clifton Eugene Hayes Jr.'
    WHERE canonical_name = 'Cliff Hayes'          AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Daniel Webster Marshall III'
    WHERE canonical_name = 'Danny Marshall'       AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Mr. David Lee Owen'
    WHERE canonical_name = 'David Owen'           AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Delores Riley Oates'
    WHERE canonical_name = 'Delores Oates'        AND cf_table_name IS NULL;
-- Eric Phillips has two CF entries with different punctuation; using formal version
UPDATE va_name_index SET cf_table_name = 'Mr. Eric Jason Phillips'
    WHERE canonical_name = 'Eric Phillips'        AND cf_table_name IS NULL;
-- Glen Sturtevant has two CF entries (period vs no period); using formal version
UPDATE va_name_index SET cf_table_name = 'Senator Glen H. Sturtevant Jr.'
    WHERE canonical_name = 'Glen Sturtevant'      AND cf_table_name IS NULL;
-- J.J. Singh files as Jasbinder Singh
UPDATE va_name_index SET cf_table_name = 'Mr. Jasbinder  Singh'
    WHERE canonical_name = 'J.J. Singh'           AND cf_table_name IS NULL;
-- Jackie Glass has two CF entries (with/without title); using formal version
UPDATE va_name_index SET cf_table_name = 'Mrs. Jacqueline Hope Glass'
    WHERE canonical_name = 'Jackie Glass'         AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Mr. Jeremy Scott McPike'
    WHERE canonical_name = 'Jeremy McPike'        AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Mr John Joseph McGuire III'
    WHERE canonical_name = 'John McGuire'         AND cf_table_name IS NULL;
-- Josh Cole files under two CF names (Hon./Rev.); using secular filing
UPDATE va_name_index SET cf_table_name = 'Hon Joshua G Cole'
    WHERE canonical_name = 'Josh Cole'            AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Joshua E. Thomas'
    WHERE canonical_name = 'Josh Thomas'          AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Mr. Justin L. Pence'
    WHERE canonical_name = 'Justin Pence'         AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Karen Fleming Hamilton'
    WHERE canonical_name = 'Karen Hamilton'       AND cf_table_name IS NULL;
-- Keith Hodges files as MYRON KEITH HODGES (all-caps in source)
UPDATE va_name_index SET cf_table_name = 'MYRON KEITH HODGES'
    WHERE canonical_name = 'Keith Hodges'         AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Ms. Kimberly Ann Taylor'
    WHERE canonical_name = 'Kim Taylor'           AND cf_table_name IS NULL;
-- Lashrecse Aird has two CF entries (with/without title); using formal version
UPDATE va_name_index SET cf_table_name = 'Mrs. Lashrecse Dianna Aird'
    WHERE canonical_name = 'Lashrecse Aird'       AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Mr. R. Lee Ware Jr.'
    WHERE canonical_name = 'Lee Ware'             AND cf_table_name IS NULL;
-- Liz Guzmán — ñ encoding issue in legislators.name; CF name is ASCII Guzman
UPDATE va_name_index SET cf_table_name = 'Mrs. Elizabeth Rosalina Guzman'
    WHERE canonical_name LIKE 'Liz Guzm%n'        AND cf_table_name IS NULL;
-- Madison Whittle has two CF entries (with/without title); using plain version
UPDATE va_name_index SET cf_table_name = 'Madison John Redd Whittle'
    WHERE canonical_name = 'Madison Whittle'      AND cf_table_name IS NULL;
-- Michelle Maldonado has two CF entries (hyphen variation); using space version
UPDATE va_name_index SET cf_table_name = 'Michelle-Ann Elizabeth Lopes Maldonado'
    WHERE canonical_name = 'Michelle Maldonado'   AND cf_table_name IS NULL;
-- Mike Cherry has two CF entries (with/without Delegate title); using plain version
UPDATE va_name_index SET cf_table_name = 'Mr. Michael A. Cherry'
    WHERE canonical_name = 'Mike Cherry'          AND cf_table_name IS NULL;
-- Mike Webert has two CF entries (with/without title); using formal version
UPDATE va_name_index SET cf_table_name = 'Delegate Michael J. Webert'
    WHERE canonical_name = 'Mike Webert'          AND cf_table_name IS NULL;
-- Otto Wachsmann has two CF entries (with/without title); using formal version
UPDATE va_name_index SET cf_table_name = 'Delegate H. Otto Wachsmann Jr.'
    WHERE canonical_name = 'Otto Wachsmann'       AND cf_table_name IS NULL;
-- Paul Milde has two CF entries (Hon./Honorable); using abbreviated version
UPDATE va_name_index SET cf_table_name = 'Hon. Paul Vincent Milde III'
    WHERE canonical_name = 'Paul Milde'           AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Phillip Andrew Scott'
    WHERE canonical_name = 'Phil Scott'           AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Richard Henry Stuart'
    WHERE canonical_name = 'Richard Stuart'       AND cf_table_name IS NULL;
-- Rip Sullivan files as Richard C. Sullivan Jr.
UPDATE va_name_index SET cf_table_name = 'Mr. Richard C. Sullivan Jr.'
    WHERE canonical_name = 'Rip Sullivan'         AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Scott Andrew Wyatt'
    WHERE canonical_name = 'Scott Wyatt'          AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Mr. Terry Lee Austin'
    WHERE canonical_name = 'Terry Austin'         AND cf_table_name IS NULL;
-- Terry Kilgore has two CF entries (with/without title); using formal version
UPDATE va_name_index SET cf_table_name = 'Delegate Terry G. Kilgore'
    WHERE canonical_name = 'Terry Kilgore'        AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Mr. Timothy Paul Griffin'
    WHERE canonical_name = 'Tim Griffin'          AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Timothy Frank French'
    WHERE canonical_name = 'Timmy French'         AND cf_table_name IS NULL;
-- Todd Gilbert's legal name is Christopher Todd Gilbert
UPDATE va_name_index SET cf_table_name = 'Christopher Todd Gilbert'
    WHERE canonical_name = 'Todd Gilbert'         AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Thomas  Garrett Jr'
    WHERE canonical_name = 'Tom Garrett'          AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Mr. Tony O. Wilt'
    WHERE canonical_name = 'Tony Wilt'            AND cf_table_name IS NULL;
-- Travis Hackworth has two CF entries (with/without Senator title); using formal version
UPDATE va_name_index SET cf_table_name = 'Senator Thurman Travis Hackworth'
    WHERE canonical_name = 'Travis Hackworth'     AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Virgil Gene Thornton Sr.'
    WHERE canonical_name = 'Virgil Thornton'      AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Mr. Wendell Scott Walker'
    WHERE canonical_name = 'Wendell Walker'       AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Mr. Wren Montgomery Williams'
    WHERE canonical_name = 'Wren Williams'        AND cf_table_name IS NULL;

-- ── Resolved via full-cycle search (all years) ──────────────────────────────

-- Jed Arnold's legal name is Jonathan E.P. Arnold (files as Mr. Jonathan E.P. Arnold)
UPDATE va_name_index SET cf_table_name = 'Mr. Jonathan E.P. Arnold'
    WHERE canonical_name = 'Jed Arnold'           AND cf_table_name IS NULL;
-- J.R. Henson's legal name is Rozia Armstrong Henson Jr (consistent 2020-2025)
UPDATE va_name_index SET cf_table_name = 'Rozia Armstrong Henson Jr'
    WHERE canonical_name = 'J.R. Henson'          AND cf_table_name IS NULL;
-- Marty Martinez's legal first name is Fernando (files as Fernando John Martinez)
UPDATE va_name_index SET cf_table_name = 'Fernando John Martinez'
    WHERE canonical_name = 'Marty Martinez'        AND cf_table_name IS NULL;
-- Tommy Wright's legal name is Thomas Cole Wright Jr (consistent 2020-2025)
UPDATE va_name_index SET cf_table_name = 'Mr. Thomas Cole Wright Jr'
    WHERE canonical_name = 'Tommy Wright'          AND cf_table_name IS NULL;
-- Will Davis (Delegate 39): William Pearson Davis is the recurring filer (2022-2026);
-- William Savoi Davis appeared only in 2023 and is a different person
UPDATE va_name_index SET cf_table_name = 'William Pearson Davis'
    WHERE canonical_name = 'Will Davis'            AND cf_table_name IS NULL;
-- Danny Diggs (Senator 24): legal name is Joseph Daniel Diggs (consistent 2021-2026)
UPDATE va_name_index SET cf_table_name = 'Mr. Joseph Daniel Diggs'
    WHERE canonical_name = 'Danny Diggs'           AND cf_table_name IS NULL;
-- Mike Jones (Senator 15): most recent formal filing is Michael J Jones (2025-2026)
UPDATE va_name_index SET cf_table_name = 'Michael J Jones'
    WHERE canonical_name = 'Mike Jones'            AND cf_table_name IS NULL;

-- Ellen McLaughlin files under her previous name Ellen Hamilton Campbell (2022-2026)
-- VPAP: https://www.vpap.org/legislators/331784-ellen-mclaughlin/
-- Committee: Campbell for Delegate - Ellen (committee ID 449342)
UPDATE va_name_index SET cf_table_name = 'Ellen Hamilton Campbell'
    WHERE canonical_name = 'Ellen McLaughlin'     AND cf_table_name IS NULL;

UPDATE va_finance_people
    SET committee_name = 'Campbell for Delegate - Ellen',
        finance_url    = 'https://www.vpap.org/committees/449342/campbell-for-delegate-ellen/',
        source_url     = 'https://www.vpap.org/legislators/331784-ellen-mclaughlin/'
    WHERE id = 527 AND committee_name IS NULL;

-- ── 2026 freshman delegates (added 2026-06-19) ────────────────────────────────
UPDATE va_name_index SET cf_table_name = 'Garrett Zitello McGuire'
    WHERE canonical_name = 'Garrett McGuire'   AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'John Chilton McAuliff'
    WHERE canonical_name = 'John McAuliff'      AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Mrs. Karen Robins Carnegie'
    WHERE canonical_name = 'Kacey Carnegie'     AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Ms. Kimberly Pope Adams'
    WHERE canonical_name = 'Kimberly Adams'     AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Mr Richard Kirk McPike'
    WHERE canonical_name = 'Kirk McPike'        AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Leslie Chambers Mehta'
    WHERE canonical_name = 'Leslie Mehta'       AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Lillian  Franklin'
    WHERE canonical_name = 'Lily Franklin'      AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Dr. Mark Collins Downey'
    WHERE canonical_name = 'Mark Downey'        AND cf_table_name IS NULL;
UPDATE va_name_index SET cf_table_name = 'Mr. Mark D. Obenshain'
    WHERE canonical_name = 'Mark Obenshain'     AND cf_table_name IS NULL;
