#!/usr/bin/env python3
"""Build FEC data for 2026 VA Senate candidates.

Creates three tables (mirrors of the VA House equivalents):
  fec_va_senate_candidates      — candidate metadata + summary financials
  fec_va_senate_contributions   — itemized individual contributions (from indiv26.zip)
  fec_va_senate_pac_contributions — PAC-to-candidate transfers (from pas226.zip)

Known 2026 VA Senate candidates hardcoded here; update if new candidates file.

Usage:
    python build_va_senate_fec_data.py
    python build_va_senate_fec_data.py --skip-download
"""
from __future__ import annotations

import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "polls.db"
INDIV_ZIP = BASE_DIR / "raw" / "indiv26.zip"
PAC_ZIP   = BASE_DIR / "raw" / "pas226.zip"
SEED_DIR  = BASE_DIR / "data"

# ── Candidate registry ──────────────────────────────────────────────────────
# Format: (cand_id, committee_id, name, party, ici)
# ici: I=Incumbent, C=Challenger, O=Open
VA_SENATE_CANDIDATES: list[tuple] = [
    ("S6VA00093", "C00438713", "WARNER, MARK R.",       "DEM", "I"),
    ("S2VA00142", "C00495358", "KAINE, TIM",            "DEM", "I"),  # 2024 cycle historical
]

# ── itcont.txt field indices (pipe-delimited) ────────────────────────────────
F_CMTE   = 0
F_NAME   = 7
F_CITY   = 8
F_STATE  = 9
F_ZIP    = 10
F_EMP    = 11
F_OCC    = 12
F_DATE   = 13
F_AMT    = 14
F_TRANID = 16
F_SUBID  = 20

# ── itpas2.txt field indices ──────────────────────────────────────────────────
P_CMTE   = 0
P_TTYPE  = 5
P_PNAME  = 7
P_PCITY  = 8
P_PSTATE = 9
P_DATE   = 13
P_AMT    = 14
P_PID    = 15
P_CANDID = 16
P_TRANID = 17
P_SUBID  = 21

# ── Sector classifier (mirrors build.sh STEP 4c3b) ───────────────────────────
_NOT_EMP  = {"NOT EMPLOYED","NONE","N/A","NA","","NOT APPLICABLE","UNEMPLOYED","NOT EMPLOY"}
_SELF_EMP = {"SELF","SELF EMPLOYED","SELF-EMPLOYED","SELFEMPLOYED"}
_RETIRED  = {"RETIRED","SEMI-RETIRED","SEMI RETIRED"}
_LAW_FIRMS = {
    "HOGAN LOVELLS","HOGAN LOVELLS US LLP","SKADDEN","SIDLEY AUSTIN",
    "WILLKIE FARR","PERKINS COIE","COVINGTON","COOLEY","PILLSBURY",
    "VENABLE","ARNOLD & PORTER","BLANK ROME","FOLEY","DECHERT",
    "HUNTON","KIRKLAND","LATHAM","MORGAN LEWIS","NORTON ROSE",
    "REED SMITH","SQUIRE PATTON","WHITE & CASE","WILEY REIN",
    "WILLIAMS MULLEN","WILLIAMS MULLENS","MCGUIREWOODS","TROUTMAN",
}


def _classify(emp_raw: str, occ_raw: str) -> str:
    emp = (emp_raw or "").strip().upper()
    occ = (occ_raw or "").strip().upper()
    e, o = emp, occ
    if e in _NOT_EMP and o in _NOT_EMP:
        return "Grassroots"
    if "HOMEMAKER" in o or "HOMEMAKER" in e:
        return "Homemaker"
    if "STUDENT" in o:
        return "Student"
    if any(r in o for r in _RETIRED) or (e in _RETIRED and o in _NOT_EMP | _RETIRED | {""}):
        return "Retired"
    if any(f in e for f in _LAW_FIRMS):
        return "Legal"
    use = o if e in (_SELF_EMP | _NOT_EMP | _RETIRED | {""}) else f"{e} {o}"
    u = use.lower()
    if any(k in u for k in ["attorney","lawyer","law firm","legal","counsel","litigation","esquire"]):
        return "Legal"
    if any(k in u for k in ["farmer","farming","agriculture","rancher","livestock","vineyard"]):
        return "Agriculture"
    if any(k in u for k in ["accountant","accounting","cpa","tax practitioner"]):
        return "Finance"
    if any(k in u for k in ["software","tech","engineer","developer","data scientist"," ai ","machine learning","cyber","cto","cio","programmer"]):
        return "Technology"
    if any(k in u for k in ["professor","teacher","university","college","school","academic","education","faculty","researcher","phd"]):
        return "Education"
    if any(k in u for k in [" md","physician","doctor","medical","hospital","healthcare","nurse","dentist","surgeon","pharmacist","psychiatrist","psychologist","therapist"]):
        return "Healthcare"
    if any(k in u for k in ["energy","petroleum","oil","gas ","solar","utility","power plant","nuclear","coal","mining"]):
        return "Energy"
    if any(k in u for k in ["consultant","consulting","strategist","advisor","policy analyst","lobbyist"]):
        return "Consulting"
    if any(k in u for k in ["investor","investment","finance","banker","financial","hedge","equity","capital","securities","wealth","fund manager","asset","portfolio","trader","cfo","economist","venture"]):
        return "Finance"
    if any(k in u for k in ["real estate","realtor","property","developer","construction","contractor","builder","architect"]):
        return "Real Estate"
    if any(k in u for k in ["writer","author","journalist","editor","publisher","media","communications","filmmaker","artist","musician","designer","creative","photographer","entertainer"]):
        return "Media/Creative"
    if any(k in u for k in ["nonprofit","non-profit","foundation","charity","ngo","social work","philanthrop","advocacy"]):
        return "Nonprofit"
    if any(k in u for k in ["government","federal","state","county","city","public sector","elected","official","military","veteran","usaid","diplomat","intelligence","police","firefighter","civil servant","intel analyst","governor","legislat","senate","congress"]):
        return "Government/Public"
    if any(k in u for k in ["candidate","campaign","political","pac "]):
        return "Political/Campaign"
    if any(k in u for k in ["executive","ceo","president","coo","managing director","vice president","founder","entrepreneur","owner","principal","chairman","chief"]):
        return "Business Executive"
    if any(k in u for k in ["sales","marketing","business","commerce","retail","manager","director","dealer","distribution"]):
        return "Business/Management"
    if any(k in u for k in ["scientist","research","laboratory","biolog","chemist","physicist","geologist"]):
        return "Research/Science"
    if e not in (_SELF_EMP | _NOT_EMP | _RETIRED | {""}):
        eu = e.lower()
        if any(k in eu for k in ["university","college","school"]):           return "Education"
        if any(k in eu for k in ["hospital","medical","health","clinic"]):    return "Healthcare"
        if any(k in eu for k in ["law","llp","legal"]):                       return "Legal"
        if any(k in eu for k in ["tech","software","systems","data","cyber"]): return "Technology"
        if any(k in eu for k in ["bank","capital","financial","investment","partners","fund","equity"]): return "Finance"
        if any(k in eu for k in ["real estate","realty","properties","property","construction"]): return "Real Estate"
        if any(k in eu for k in ["foundation","nonprofit","non-profit","charitable"]): return "Nonprofit"
        if any(k in eu for k in ["energy","petroleum","oil","gas","solar","power","electric"]): return "Energy"
        if any(k in eu for k in ["consulting","advisors","strategies","advisory"]): return "Consulting"
        return "Other (named employer)"
    if e in _SELF_EMP:
        return "Self-Employed (Other)"
    if e in _NOT_EMP or e == "":
        return "Grassroots"
    return "Other"


def _parse_date(raw: str) -> str:
    try:
        return datetime.strptime(raw.strip(), "%m%d%Y").strftime("%Y-%m-%d")
    except Exception:
        return raw.strip()


# ── DDL ──────────────────────────────────────────────────────────────────────
DDL_CANDS = """
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
"""

DDL_CONTRIBS = """
CREATE TABLE IF NOT EXISTS fec_va_senate_contributions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    cmte_id      TEXT,
    cand_id      TEXT,
    cand_name    TEXT,
    party        TEXT,
    ici          TEXT,
    contributor  TEXT,
    city         TEXT,
    state        TEXT,
    zip          TEXT,
    employer     TEXT,
    occupation   TEXT,
    sector       TEXT,
    contrib_date TEXT,
    amount       REAL,
    tran_id      TEXT,
    sub_id       TEXT,
    cycle        INTEGER DEFAULT 2026,
    loaded_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_vasc_cand   ON fec_va_senate_contributions(cand_id);
CREATE INDEX IF NOT EXISTS idx_vasc_state  ON fec_va_senate_contributions(state);
CREATE INDEX IF NOT EXISTS idx_vasc_amount ON fec_va_senate_contributions(amount);
CREATE INDEX IF NOT EXISTS idx_vasc_sector ON fec_va_senate_contributions(sector);
CREATE UNIQUE INDEX IF NOT EXISTS uix_vasc_tran ON fec_va_senate_contributions(tran_id) WHERE tran_id != '';
"""

DDL_PACS = """
CREATE TABLE IF NOT EXISTS fec_va_senate_pac_contributions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    cmte_id          TEXT,
    cand_id          TEXT,
    cand_name        TEXT,
    party            TEXT,
    ici              TEXT,
    pac_id           TEXT,
    pac_name         TEXT,
    pac_city         TEXT,
    pac_state        TEXT,
    amount           REAL,
    contrib_date     TEXT,
    transaction_type TEXT,
    tran_id          TEXT,
    sub_id           TEXT,
    cycle            INTEGER DEFAULT 2026,
    loaded_at        TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uix_vaspc_tran ON fec_va_senate_pac_contributions(tran_id) WHERE tran_id != '';
CREATE INDEX IF NOT EXISTS idx_vaspc_cand ON fec_va_senate_pac_contributions(cand_id);
"""


def _seed_candidates(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL_CANDS)
    conn.execute("DELETE FROM fec_va_senate_candidates")
    conn.executemany(
        "INSERT OR REPLACE INTO fec_va_senate_candidates"
        " (cand_id,committee_id,name,party,ici,cycle) VALUES (?,?,?,?,?,2026)",
        [(c[0], c[1], c[2], c[3], c[4]) for c in VA_SENATE_CANDIDATES],
    )
    conn.commit()
    print(f"  Seeded {len(VA_SENATE_CANDIDATES)} Senate candidates")


def _load_contributions(conn: sqlite3.Connection) -> int:
    conn.executescript(DDL_CONTRIBS)
    conn.execute("DELETE FROM fec_va_senate_contributions")
    conn.commit()

    cmte_to_meta = {c[1]: {"cand_id": c[0], "name": c[2], "party": c[3], "ici": c[4]}
                    for c in VA_SENATE_CANDIDATES}
    target = set(cmte_to_meta.keys())

    now = datetime.utcnow().isoformat() + "+00:00"
    total = 0
    batch: list[tuple] = []

    with zipfile.ZipFile(INDIV_ZIP) as z:
        fname = next(n for n in z.namelist() if n.endswith(".txt"))
        with z.open(fname) as f:
            for line_bytes in f:
                try:
                    parts = line_bytes.decode("latin-1").rstrip("\r\n").split("|")
                except Exception:
                    continue
                if len(parts) < 15:
                    continue
                cmte = parts[F_CMTE].strip()
                if cmte not in target:
                    continue
                meta = cmte_to_meta[cmte]
                try:
                    amt = float(parts[F_AMT]) if parts[F_AMT].strip() else 0.0
                except ValueError:
                    amt = 0.0
                tran_id = parts[F_TRANID].strip() if len(parts) > F_TRANID else ""
                sub_id  = parts[F_SUBID].strip()  if len(parts) > F_SUBID  else ""
                emp = parts[F_EMP].strip()
                occ = parts[F_OCC].strip()
                batch.append((
                    cmte, meta["cand_id"], meta["name"], meta["party"], meta["ici"],
                    parts[F_NAME].strip(), parts[F_CITY].strip(), parts[F_STATE].strip(),
                    parts[F_ZIP].strip(), emp, occ, _classify(emp, occ),
                    _parse_date(parts[F_DATE]), amt, tran_id, sub_id, 2026, now,
                ))
                if len(batch) >= 5000:
                    conn.executemany(
                        "INSERT OR IGNORE INTO fec_va_senate_contributions"
                        " (cmte_id,cand_id,cand_name,party,ici,contributor,city,state,zip,"
                        "employer,occupation,sector,contrib_date,amount,tran_id,sub_id,cycle,loaded_at)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        batch,
                    )
                    conn.commit()
                    total += len(batch)
                    batch.clear()
                    if total % 10000 == 0:
                        print(f"  {total:,} rows...", flush=True)

    if batch:
        conn.executemany(
            "INSERT OR IGNORE INTO fec_va_senate_contributions"
            " (cmte_id,cand_id,cand_name,party,ici,contributor,city,state,zip,"
            "employer,occupation,sector,contrib_date,amount,tran_id,sub_id,cycle,loaded_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            batch,
        )
        conn.commit()
        total += len(batch)

    return total


def _load_pac_contributions(conn: sqlite3.Connection) -> int:
    conn.executescript(DDL_PACS)
    conn.execute("DELETE FROM fec_va_senate_pac_contributions")
    conn.commit()

    target_cand_ids = {c[0] for c in VA_SENATE_CANDIDATES}
    cand_meta = {c[0]: {"name": c[2], "party": c[3], "ici": c[4]}
                 for c in VA_SENATE_CANDIDATES}

    now = datetime.utcnow().isoformat() + "+00:00"
    rows: list[dict] = []
    seen: set[str] = set()

    with zipfile.ZipFile(PAC_ZIP) as z:
        with z.open("itpas2.txt") as f:
            for line_bytes in f:
                parts = line_bytes.decode("latin-1").rstrip("\r\n").split("|")
                if len(parts) < 18:
                    continue
                cand_id = parts[P_CANDID].strip()
                if cand_id not in target_cand_ids:
                    continue
                tran_id = parts[P_TRANID].strip()
                if tran_id and tran_id in seen:
                    continue
                if tran_id:
                    seen.add(tran_id)
                try:
                    amt = float(parts[P_AMT].strip())
                except (ValueError, IndexError):
                    continue
                meta = cand_meta[cand_id]
                sub_id = parts[P_SUBID].strip() if len(parts) > P_SUBID else ""
                rows.append({
                    "cmte_id":  parts[P_CMTE].strip(),
                    "cand_id":  cand_id,
                    "cand_name": meta["name"],
                    "party":    meta["party"],
                    "ici":      meta["ici"],
                    "pac_id":   parts[P_PID].strip(),
                    "pac_name": parts[P_PNAME].strip(),
                    "pac_city": parts[P_PCITY].strip(),
                    "pac_state": parts[P_PSTATE].strip(),
                    "amount":   amt,
                    "contrib_date": _parse_date(parts[P_DATE]),
                    "transaction_type": parts[P_TTYPE].strip(),
                    "tran_id":  tran_id,
                    "sub_id":   sub_id,
                    "cycle":    2026,
                    "loaded_at": now,
                })

    conn.executemany(
        "INSERT OR IGNORE INTO fec_va_senate_pac_contributions"
        " (cmte_id,cand_id,cand_name,party,ici,pac_id,pac_name,pac_city,pac_state,"
        "amount,contrib_date,transaction_type,tran_id,sub_id,cycle,loaded_at)"
        " VALUES (:cmte_id,:cand_id,:cand_name,:party,:ici,:pac_id,:pac_name,:pac_city,"
        ":pac_state,:amount,:contrib_date,:transaction_type,:tran_id,:sub_id,:cycle,:loaded_at)",
        rows,
    )
    conn.commit()
    return len(rows)


def _update_totals(conn: sqlite3.Connection) -> None:
    """Back-fill total_receipts / cash figures from aggregated contribution data."""
    for cand_id, *_ in VA_SENATE_CANDIDATES:
        row = conn.execute(
            "SELECT ROUND(SUM(amount),0), COUNT(*) FROM fec_va_senate_contributions"
            " WHERE cand_id=? AND amount>0", (cand_id,)
        ).fetchone()
        indiv_total = row[0] or 0
        pac_row = conn.execute(
            "SELECT ROUND(SUM(amount),0) FROM fec_va_senate_pac_contributions"
            " WHERE cand_id=? AND amount>0", (cand_id,)
        ).fetchone()
        pac_total = pac_row[0] or 0
        total = indiv_total + pac_total
        conn.execute(
            "UPDATE fec_va_senate_candidates SET total_receipts=?, ind_contributions=?"
            " WHERE cand_id=?",
            (total, indiv_total, cand_id),
        )
    conn.commit()


def _export_seed(conn: sqlite3.Connection) -> None:
    SEED_DIR.mkdir(exist_ok=True)
    now_str = datetime.utcnow().strftime("%Y-%m-%d")

    def esc(v):
        if v is None: return "NULL"
        if isinstance(v, (int, float)): return str(v)
        return "'" + str(v).replace("'", "''") + "'"

    # candidates seed
    crows = conn.execute(
        "SELECT cand_id,committee_id,name,party,state,ici,cycle,"
        "total_receipts,ind_contributions,total_disbursements,cash_on_hand"
        " FROM fec_va_senate_candidates"
    ).fetchall()
    clines = [
        f"-- VA Senate FEC candidates seed | {now_str}",
        DDL_CANDS.strip(),
        "DELETE FROM fec_va_senate_candidates;",
    ]
    for r in crows:
        clines.append(
            "INSERT OR REPLACE INTO fec_va_senate_candidates"
            " (cand_id,committee_id,name,party,state,ici,cycle,"
            "total_receipts,ind_contributions,total_disbursements,cash_on_hand)"
            f" VALUES({','.join(esc(x) for x in r)});"
        )
    (SEED_DIR / "fec_va_senate_candidates_seed.sql").write_text("\n".join(clines), encoding="utf-8")

    # contributions seed
    contribs = conn.execute(
        "SELECT cmte_id,cand_id,cand_name,party,ici,contributor,city,state,zip,"
        "employer,occupation,sector,contrib_date,amount,tran_id,sub_id,cycle,loaded_at"
        " FROM fec_va_senate_contributions ORDER BY cand_id,amount DESC"
    ).fetchall()
    cols = "(cmte_id,cand_id,cand_name,party,ici,contributor,city,state,zip,employer,occupation,sector,contrib_date,amount,tran_id,sub_id,cycle,loaded_at)"
    vals = ",\n".join(f"    ({','.join(esc(x) for x in r)})" for r in contribs)
    contrib_sql = (
        f"-- VA Senate itemized contributions seed | {now_str} | {len(contribs):,} rows\n"
        + DDL_CONTRIBS.strip() + "\n"
        + "DELETE FROM fec_va_senate_contributions;\n"
        + f"INSERT OR IGNORE INTO fec_va_senate_contributions {cols}\nVALUES\n{vals};\n"
    )
    seed_path = SEED_DIR / "fec_va_senate_contributions_seed.sql"
    seed_path.write_text(contrib_sql, encoding="utf-8")
    mb = seed_path.stat().st_size / 1e6
    print(f"  Contributions seed -> {seed_path} ({mb:.1f} MB, {len(contribs):,} rows)")

    # PAC seed
    prows = conn.execute(
        "SELECT cmte_id,cand_id,cand_name,party,ici,pac_id,pac_name,pac_city,pac_state,"
        "amount,contrib_date,transaction_type,tran_id,sub_id,cycle,loaded_at"
        " FROM fec_va_senate_pac_contributions ORDER BY amount DESC"
    ).fetchall()
    pcols = "(cmte_id,cand_id,cand_name,party,ici,pac_id,pac_name,pac_city,pac_state,amount,contrib_date,transaction_type,tran_id,sub_id,cycle,loaded_at)"
    pvals = ",\n".join(f"    ({','.join(esc(x) for x in r)})" for r in prows)
    pac_sql = (
        f"-- VA Senate PAC contributions seed | {now_str} | {len(prows):,} rows\n"
        + DDL_PACS.strip() + "\n"
        + "DELETE FROM fec_va_senate_pac_contributions;\n"
        + f"INSERT OR IGNORE INTO fec_va_senate_pac_contributions {pcols}\nVALUES\n{pvals};\n"
    )
    (SEED_DIR / "fec_va_senate_pac_contributions_seed.sql").write_text(pac_sql, encoding="utf-8")
    print(f"  PAC seed -> {SEED_DIR}/fec_va_senate_pac_contributions_seed.sql ({len(prows)} rows)")


def main() -> None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    print("=== Step 1: Seed candidate metadata ===")
    _seed_candidates(conn)

    print("\n=== Step 2: Load individual contributions from indiv26.zip ===")
    n_contribs = _load_contributions(conn)
    print(f"  Loaded {n_contribs:,} individual contributions")

    print("\n=== Step 3: Load PAC contributions from pas226.zip ===")
    n_pacs = _load_pac_contributions(conn)
    print(f"  Loaded {n_pacs} PAC contributions")

    print("\n=== Step 4: Update candidate financial totals ===")
    _update_totals(conn)

    # Print summary
    print("\n=== Summary ===")
    for r in conn.execute(
        "SELECT name, total_receipts, ind_contributions"
        " FROM fec_va_senate_candidates ORDER BY total_receipts DESC NULLS LAST"
    ).fetchall():
        print(f"  {r[0]:<28} total=${r[1]:>12,.0f}  indiv=${r[2]:>12,.0f}")

    print("\n=== Step 5: Export seed SQL ===")
    _export_seed(conn)

    conn.close()
    print("\nDone. Next: add seed steps to build.sh and commit.")


if __name__ == "__main__":
    main()
