-- VA Senate FEC candidates seed | 2026-06-14
CREATE TABLE IF NOT EXISTS fec_va_senate_candidates (
    cand_id          TEXT PRIMARY KEY,
    committee_id     TEXT,
    name             TEXT,
    party            TEXT,
    state            TEXT DEFAULT 'VA',
    ici              TEXT,
    cycle            INTEGER DEFAULT 2026,
    total_receipts   REAL,
    ind_contributions REAL,
    total_disbursements REAL,
    cash_on_hand     REAL
);
DELETE FROM fec_va_senate_candidates;
INSERT OR REPLACE INTO fec_va_senate_candidates (cand_id,committee_id,name,party,state,ici,cycle,total_receipts,ind_contributions,total_disbursements,cash_on_hand) VALUES('S6VA00093','C00438713','WARNER, MARK R.','DEM','VA','I',2026,7310179.0,5412449.0,NULL,NULL);
INSERT OR REPLACE INTO fec_va_senate_candidates (cand_id,committee_id,name,party,state,ici,cycle,total_receipts,ind_contributions,total_disbursements,cash_on_hand) VALUES('S2VA00142','C00495358','KAINE, TIM','DEM','VA','I',2026,431548.0,276582.0,NULL,NULL);