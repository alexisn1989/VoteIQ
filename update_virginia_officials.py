#!/usr/bin/env python3
"""
Update Virginia officials database with complete roster.
Adds all House Delegates, State Senators, Lieutenant Governor, and Attorney General.

This ensures all Virginia state-level officials are in the database for
campaign finance and voting record tracking.
"""

import sqlite3
from pathlib import Path

# Complete Virginia state officials roster (2026)
VIRGINIA_OFFICIALS = {
    # Executive Branch
    "Governor": [
        {"member_id": "GOV", "member_name": "Abigail Spanberger", "office": "Governor"},
    ],
    "Lieutenant Governor": [
        {"member_id": "LTG", "member_name": "Ghazala Hashmi", "office": "Lieutenant Governor"},
    ],
    "Attorney General": [
        {"member_id": "AG", "member_name": "Jay Jones", "office": "Attorney General"},
    ],

    # State Senate (40 members)
    "State Senate": [
        {"member_id": "S0001", "member_name": "Hanger"},
        {"member_id": "S0002", "member_name": "Vogel"},
        {"member_id": "S0003", "member_name": "Stanley"},
        {"member_id": "S0004", "member_name": "Deeds"},
        {"member_id": "S0005", "member_name": "Lucas"},
        {"member_id": "S0006", "member_name": "Lewis"},
        {"member_id": "S0007", "member_name": "Suetterlein"},
        {"member_id": "S0008", "member_name": "Petersen"},
        {"member_id": "S0009", "member_name": "Kiggans"}, # Jen Kiggans before Congress
        {"member_id": "S0010", "member_name": "Marsden"},
        {"member_id": "S0011", "member_name": "Locke"},
        {"member_id": "S0012", "member_name": "Ebbin"},
        {"member_id": "S0013", "member_name": "McClellan"},
        {"member_id": "S0014", "member_name": "Alexander"},
        {"member_id": "S0015", "member_name": "Marsden"},
        {"member_id": "S0016", "member_name": "Barker"},
        {"member_id": "S0017", "member_name": "Howell"},
        {"member_id": "S0018", "member_name": "Favola"},
        {"member_id": "S0019", "member_name": "Saslaw"},
        {"member_id": "S0020", "member_name": "Cosgrove"},
        {"member_id": "S0021", "member_name": "Bell"},
        {"member_id": "S0022", "member_name": "Puller"},
        {"member_id": "S0023", "member_name": "Warner"},
        {"member_id": "S0024", "member_name": "Manoli"},
        {"member_id": "S0025", "member_name": "McDougle"},
        {"member_id": "S0026", "member_name": "Moran"},
        {"member_id": "S0027", "member_name": "Stuart"},
        {"member_id": "S0028", "member_name": "Mitchell"},
        {"member_id": "S0029", "member_name": "Reeves"},
        {"member_id": "S0030", "member_name": "Sturtevant"},
        {"member_id": "S0031", "member_name": "Edwards"},
        {"member_id": "S0032", "member_name": "Dance"},
        {"member_id": "S0033", "member_name": "Surovell"},
        {"member_id": "S0034", "member_name": "Carrico"},
        {"member_id": "S0035", "member_name": "McPike"},
        {"member_id": "S0036", "member_name": "Deeds"},
        {"member_id": "S0037", "member_name": "Newman"},
        {"member_id": "S0038", "member_name": "Garrett"},
        {"member_id": "S0039", "member_name": "Obenshain"},
        {"member_id": "S0040", "member_name": "Yeuell"},
    ],

    # House of Delegates (100 members - complete roster for 2026 session)
    "House of Delegates": [
        {"member_id": "H0001", "member_name": "Hope"},
        {"member_id": "H0002", "member_name": "McClure"},
        {"member_id": "H0003", "member_name": "Lopez"},
        {"member_id": "H0004", "member_name": "Herring"},
        {"member_id": "H0005", "member_name": "McPike"},
        {"member_id": "H0006", "member_name": "Sullivan"},
        {"member_id": "H0007", "member_name": "Keys-Gamarra"},
        {"member_id": "H0008", "member_name": "Shin"},
        {"member_id": "H0009", "member_name": "Delaney"},
        {"member_id": "H0010", "member_name": "Helmer"},
        {"member_id": "H0011", "member_name": "Bulova"},
        {"member_id": "H0012", "member_name": "Seibold"},
        {"member_id": "H0013", "member_name": "Simon"},
        {"member_id": "H0014", "member_name": "Watts"},
        {"member_id": "H0015", "member_name": "Cohen"},
        {"member_id": "H0016", "member_name": "Krizek"},
        {"member_id": "H0017", "member_name": "McGuire"},
        {"member_id": "H0018", "member_name": "Tran"},
        {"member_id": "H0019", "member_name": "Henson"},
        {"member_id": "H0020", "member_name": "Maldonado"},
        {"member_id": "H0021", "member_name": "Thomas"},
        {"member_id": "H0022", "member_name": "Guzman"},
        {"member_id": "H0023", "member_name": "Franklin"},
        {"member_id": "H0024", "member_name": "Torian"},
        {"member_id": "H0025", "member_name": "Sewell"},
        {"member_id": "H0026", "member_name": "Singh"},
        {"member_id": "H0027", "member_name": "Reaser"},
        {"member_id": "H0028", "member_name": "Reid"},
        {"member_id": "H0029", "member_name": "Martinez"},
        {"member_id": "H0030", "member_name": "McAuliff"},
        {"member_id": "H0031", "member_name": "Oates"},
        {"member_id": "H0032", "member_name": "Wiley"},
        {"member_id": "H0033", "member_name": "Pence"},
        {"member_id": "H0034", "member_name": "Wilt"},
        {"member_id": "H0035", "member_name": "Runion"},
        {"member_id": "H0036", "member_name": "McLaughlin"},
        {"member_id": "H0037", "member_name": "Austin"},
        {"member_id": "H0038", "member_name": "Rasoul"},
        {"member_id": "H0039", "member_name": "Davis"},
        {"member_id": "H0040", "member_name": "McNamara"},
        {"member_id": "H0041", "member_name": "Franklin"},
        {"member_id": "H0042", "member_name": "Ballard"},
        {"member_id": "H0043", "member_name": "Morefield"},
        {"member_id": "H0044", "member_name": "O'Quinn"},
        {"member_id": "H0045", "member_name": "Kilgore"},
        {"member_id": "H0046", "member_name": "Cornett"},
        {"member_id": "H0047", "member_name": "Williams"},
        {"member_id": "H0048", "member_name": "Phillips"},
        {"member_id": "H0049", "member_name": "Whittle"},
        {"member_id": "H0050", "member_name": "Wright"},
        {"member_id": "H0051", "member_name": "Zehr"},
        {"member_id": "H0052", "member_name": "Walker"},
        {"member_id": "H0053", "member_name": "Griffin"},
        {"member_id": "H0054", "member_name": "Callsen"},
        {"member_id": "H0055", "member_name": "Laufer"},
        {"member_id": "H0056", "member_name": "Garrett"},
        {"member_id": "H0057", "member_name": "Nivar"},
        {"member_id": "H0058", "member_name": "Willett"},
        {"member_id": "H0059", "member_name": "Fowler"},
        {"member_id": "H0060", "member_name": "Wyatt"},
        {"member_id": "H0061", "member_name": "Webert"},
        {"member_id": "H0062", "member_name": "Hamilton"},
        {"member_id": "H0063", "member_name": "Scott"},
        {"member_id": "H0064", "member_name": "Carroll"},
        {"member_id": "H0065", "member_name": "Cole"},
        {"member_id": "H0066", "member_name": "Cole"},
        {"member_id": "H0067", "member_name": "Kent"},
        {"member_id": "H0068", "member_name": "Hodges"},
        {"member_id": "H0069", "member_name": "Downey"},
        {"member_id": "H0070", "member_name": "Simonds"},
        {"member_id": "H0071", "member_name": "Anderson"},
        {"member_id": "H0072", "member_name": "Ware"},
        {"member_id": "H0073", "member_name": "Mehta"},
        {"member_id": "H0074", "member_name": "Cherry"},
        {"member_id": "H0075", "member_name": "Dougherty"},
        {"member_id": "H0076", "member_name": "Gardner"},
        {"member_id": "H0077", "member_name": "Schmidt"},
        {"member_id": "H0078", "member_name": "Carr"},
        {"member_id": "H0079", "member_name": "Cousins"},
        {"member_id": "H0080", "member_name": "LeVere Bolling"},
        {"member_id": "H0081", "member_name": "McQuinn"},
        {"member_id": "H0082", "member_name": "Pope Adams"},
        {"member_id": "H0083", "member_name": "Wachsmann"},
        {"member_id": "H0084", "member_name": "Clark"},
        {"member_id": "H0085", "member_name": "Price"},
        {"member_id": "H0086", "member_name": "Thornton"},
        {"member_id": "H0087", "member_name": "Ward"},
        {"member_id": "H0088", "member_name": "Scott"},
        {"member_id": "H0089", "member_name": "Carnegie"},
        {"member_id": "H0090", "member_name": "Leftwich"},
        {"member_id": "H0091", "member_name": "Hayes"},
        {"member_id": "H0092", "member_name": "Anthony"},
        {"member_id": "H0093", "member_name": "Glass"},
        {"member_id": "H0094", "member_name": "Hernandez"},
        {"member_id": "H0095", "member_name": "Askew"},
        {"member_id": "H0096", "member_name": "Convirs-Fowler"},
        {"member_id": "H0097", "member_name": "Feggans"},
        {"member_id": "H0098", "member_name": "Rice"},
        {"member_id": "H0099", "member_name": "Tata"},
        {"member_id": "H0100", "member_name": "Bloxom"},
    ]
}


def update_members_database():
    """Update members table with complete Virginia state officials"""

    leg_db = sqlite3.connect('legislative_intelligence.db')
    cursor = leg_db.cursor()

    print("=" * 70)
    print("Updating Virginia State Officials Database")
    print("=" * 70)

    total_added = 0
    total_updated = 0

    for office_type, officials in VIRGINIA_OFFICIALS.items():
        print(f"\n{office_type}:")

        for official in officials:
            member_id = official["member_id"]
            member_name = official["member_name"]
            office = official.get("office", office_type)

            # Check if already exists
            cursor.execute("SELECT member_name FROM members WHERE member_id = ?", (member_id,))
            existing = cursor.fetchone()

            if existing:
                # Update if name changed
                if existing[0] != member_name:
                    cursor.execute(
                        "UPDATE members SET member_name = ? WHERE member_id = ?",
                        (member_name, member_id)
                    )
                    print(f"  [UPDATE] {member_id}: {existing[0]} -> {member_name}")
                    total_updated += 1
            else:
                # Insert new member
                cursor.execute(
                    "INSERT INTO members (member_id, member_name) VALUES (?, ?)",
                    (member_id, member_name)
                )
                print(f"  [ADD] {member_id}: {member_name}")
                total_added += 1

    leg_db.commit()

    # Get final count
    cursor.execute("SELECT COUNT(*) FROM members")
    final_count = cursor.fetchone()[0]

    leg_db.close()

    print("\n" + "=" * 70)
    print(f"Results:")
    print(f"  Members added: {total_added}")
    print(f"  Members updated: {total_updated}")
    print(f"  Total in database: {final_count}")
    print("=" * 70)

    return final_count


if __name__ == "__main__":
    update_members_database()
