-- VB split (non-unanimous) votes seed (auto-generated from local polls.db)
-- Loaded idempotently at runtime startup. Additive only.
BEGIN TRANSACTION;
DROP TABLE IF EXISTS "vb_split_votes";
CREATE TABLE vb_split_votes (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            meeting_date  TEXT NOT NULL,
            meeting_id    INTEGER NOT NULL,
            resolution_id INTEGER NOT NULL,
            title         TEXT NOT NULL,
            topic         TEXT,
            yes_count     INTEGER,
            no_count      INTEGER,
            no_voters     TEXT,
            result        TEXT,
            updated_at    TEXT DEFAULT (datetime('now')),
            UNIQUE(meeting_id, resolution_id)
        );
INSERT INTO "vb_split_votes" VALUES(157,'2026-04-21',1655,5490,'CITY OF VIRGINIA BEACH - AN ORDINANCE TO ADOPT THE CITY OF VIRGINIA BEACH COMPREHENSIVE PLAN- imagin','rezoning',3,8,'Berlucchi, Dyer, Cummings, Hutcheson, Remick, Ross-Hammond, Wilson, Schulman','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(158,'2026-04-16',1712,5513,'Temporary Curfew',NULL,10,1,'Rouse','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(159,'2026-03-24',1652,5456,'HARRISON AND LEAR, INC. / JBWK LLC / WHITE CLOVER, LLC',NULL,3,8,'Berlucchi, Hutcheson, Jackson-Green, Remick, Ross-Hammond, Rouse, Wilson, Schulman','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(160,'2025-10-21',1568,5148,'Ordinance 1) Declaring City Property Located at 333 Laskin Road to be in Excess of the City''s Needs','infrastructure',10,1,'Wilson','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(161,'2025-10-21',1568,5130,'ASC Real Estate LLC (Margaret Shaia) Property Owner: City of Virginia Beach Development Authority Co','development',10,1,'Henley','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(162,'2025-07-08',1562,4897,'Heartsille Reynolds, Portia McGraw, Sharon McPherson, Edward Thomas, & Ronald Hayes, Trustees for Tu',NULL,2,9,'Berlucchi, Dyer, Cummings, Hutcheson, Jackson-Green, Remick, Ross-Hammond, Wilson, Schulman','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(163,'2025-07-08',1562,4896,'Keelingwood Apartments, LLC Property Owners: Keelingwood Apartments, LLC & Southern23, LLC Modificat',NULL,9,2,'Henley, Rouse','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(164,'2025-07-08',1562,4886,'Virginia Beach Beacon Baptist Church & CFT NV Developments, LLC [Applicant] Virginia Beach Beacon Ba','development',10,1,'Henley','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(165,'2025-05-13',1613,4725,'1.	Ordinance to APPROPRIATE $3,864,748,322 consisting of $753,894,590 in inter-fund transfers, $325,','budget',10,1,'Henley','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(166,'2025-05-06',1557,4716,'Resolution Requesting the Circuit Court to Order that a Referendum Election Be Held on November 4, 2',NULL,7,4,'Hutcheson, Remick, Rouse, Schulman','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(167,'2025-02-18',1552,4541,'Ordinance Approving Application of 33rd Street, LLC for the Closure of Two Contiguous Air Rights Par',NULL,10,1,'Henley','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(168,'2025-02-04',1551,4500,'Green Clean Holland, LLC Property Owners: Alp & Alex Aslan Conditional Use Permit (Car Wash Facility','rezoning',4,6,'Berlucchi, Dyer, Cummings, Jackson-Green, Wilson, Schulman','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(169,'2025-01-07',1549,4429,'Resolution to DIRECT the City Manager to PROVIDE Something in the Water, LLC (SITW) with Notice of B',NULL,8,2,'Henley, Jackson-Green','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(170,'2024-11-12',1504,4308,'Resolution To Request the General Assembly Amend the City Charter to Implement a Ten-Single Member D',NULL,7,4,'Berlucchi, Dyer, Henley, Wilson','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(171,'2024-10-01',1502,4223,'PLA - Adekoje PJ 22001, LLC (Applicant & Property Owner) Conditional Rezoning (AG-1 Agricultural to','rezoning',5,6,'Hutcheson, Ross-Hammond, Rouse, Wilson, Schulman','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(172,'2024-09-17',1501,4202,'Mary Lively (Applicant & Property Owner) Subdivision Variance (Section 4.4(b) of the Subdivision Reg','rezoning',9,2,'Henley','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(173,'2024-09-03',1500,4165,'Thalia Plaza, LLC (Applicant & Property Owner) Conditional Rezoning (B-2 Community Business District','rezoning',8,2,'Henley','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(174,'2024-08-20',1499,4134,'Green Clean Holland, LLC (Applicant) Alp & Alex Aslan (Property Owners) Conditional Use Permit (Car','rezoning',7,3,'Ross-Hammond, Rouse','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(175,'2024-07-02',1496,4033,'Resolution 1) Approving Modifications to a Term Sheet Relating to the Redevelopment of Pembroke Mall','development',9,1,'','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(176,'2024-06-18',1495,4008,'Resolution to ESTABLISH a Stormwater Management Implementation Advisory Group','infrastructure',9,2,'','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(177,'2024-06-18',1495,3988,'Ordinance to Transfer Vacancy Savings in the General Fund and to Authorize a Grant of $37,713 to Fir',NULL,10,1,'Henley','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(178,'2024-06-18',1495,3976,'Carl R. Ellis, Jr (Applicant & Property Owner) Subdivision Variance (Section 4.4(b) of the Subdivisi','rezoning',10,1,'','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(179,'2024-05-14',1534,3888,'Ordinance to APPROPRIATE $3,616,233,616 consisting of $678,967,053 in inter-fund transfers, $297,883','budget',10,1,'','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(180,'2024-04-30',1531,3864,'AUTHORIZE Collective Bargaining','governance',5,5,'Berlucchi, Dyer, Henley, Wilson','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(181,'2024-04-16',1491,3837,'PLA - City of Virginia Beach – An ordinance to amend Section 111 of the City Zoning Ordinance pertai','governance',9,2,'','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(182,'2024-04-16',1491,3831,'PLA - Wycliffe Presbyterian Church & BHC, LLC (Applicants) Trustees of Wycliffe Presbyterian Church',NULL,7,4,'Henley, Rouse','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(183,'2024-01-16',1485,3657,'Ordinance to TRANSFER $25,000 re 40th Annual Martin Lurther King, Jr. Awards Breakfast',NULL,8,1,'','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(184,'2024-01-16',1485,3651,'Resolution to Direct the City Manager to Undertake an Economic Impact Study and Infrastructure Cost','infrastructure',7,4,'Henley, Rouse','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(185,'2023-11-21',1425,3529,'Wycliffe Presbyterian Church (Applicant & Property Owner) Modification of Conditions (Religious Use)',NULL,9,1,'','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(186,'2023-11-21',1425,3518,'Ordinance to Authorize the Vacation of a 20’ Public Access Easement Located Along the Eastern Bounda','development',9,1,'','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(187,'2023-11-21',1425,3512,'Ordinance to Add Section 38-9 to the City Code to Prohibit Weapons at the Housing Resource Center',NULL,6,4,'Berlucchi, Dyer, Wilson','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(188,'2023-11-14',1424,3497,'Traci R. McGlynn & Michael L McGlynn (Applicant & Property Owner) Conditional Use Permit (Short Term','rezoning',8,1,'','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(189,'2023-11-14',1424,3496,'Ashley Guller (Applicant & Property Owner) Conditional Use Permit (Short Term Rental) Address: 921 P','rezoning',8,1,'','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(190,'2023-11-14',1424,3495,'The Fountain, LLC (Applicant & Property Owner) Conditional Use Permits (Short Term Rentals) Addresse','rezoning',8,1,'','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(191,'2023-11-14',1424,3483,'Ordinance to Establish Capital Project #100667 “Rudee Loop Park Development,” and to Transfer $4,000','development',7,2,'Henley','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(192,'2023-10-17',1422,3417,'Ordinance to Amend City Code Section 35.3-10 to Extend the Sunset Provision for the Chesopeian Colon','governance',7,2,'Henley','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(193,'2023-09-19',1418,3344,'Ordinance to ADOPT a City Council Policy re Legislative Agenda Process and Adoption Requirements','governance',4,7,'Berlucchi, Dyer, Remick, Ross-Hammond, Wilson, Schulman','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(194,'2023-09-19',1418,3329,'Ordinance to Amend Section 21-407 of the City Code Pertaining to Charges for Towing and Storage of V','governance',9,2,'','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(195,'2023-09-19',1418,3310,'SPEEDGEARZ, LLC [Applicant] COVINGTON FAMILY TRUST [PROPERTY OWNER] Conditional Use Permit (Automobi','rezoning',10,1,'','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(196,'2023-09-19',1418,3308,'JAMIE GEORGE [Applicant & Property Owner] Conditional Use Permit (Short Term Rental) for the propert','rezoning',8,3,'Henley','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(197,'2023-09-19',1418,3307,'NICHOLAS IULIANO [Applicant & Property Owner] Conditional Use Permit (Short Term Rental) for the pro','rezoning',9,2,'Henley','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(198,'2023-08-15',1414,3236,'ORD - Appropriate Funds from Tourism Investment Program Fund for Pride at the Beach 2024','budget',10,1,'Henley','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(199,'2023-08-15',1414,3208,'ORD - Appropriate $750k Auth CM Sponsor Agreement Audacy Virginia LLC','budget',7,4,'Henley','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(200,'2023-08-15',1414,3207,'ORD - Adopt The 2023 Redistricting Plan And To Authorize Implementation Of A Ten Single-Member Syste',NULL,10,1,'Henley','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(201,'2023-08-15',1414,3204,'ORD - (1) Establishing Capital Project #100666 “Dam Neck Road-London Bridge Connector Improvements,”','defense',10,1,'','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(202,'2023-07-11',1411,3128,'ORD - Appropriate Funds from the Tourism Investment Program Fund Balance ad to Award a $100,000 Gran','budget',9,1,'Henley','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(203,'2023-07-11',1411,3115,'PLA - THOMAS A. BROWN [Applicant & Property Owner] Subdivision Variance','rezoning',9,1,'','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(204,'2023-07-11',1411,3114,'PLA - THOMAS A. BROWN [Applicant & Property Owner] Subdivision Variance (Section 4.4(b) & (d) of the','rezoning',9,1,'','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(205,'2023-05-16',1395,3010,'RES - Outlining the Process to Occur upon Completion of the Interim Agreement and Future',NULL,9,1,'','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(206,'2023-04-04',1389,2900,'An Ordinance to Extend the Sunset Date of Article III, Division 5 of Chapter 21 of the City Code Per',NULL,10,1,'Henley','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(207,'2023-03-21',1387,2861,'Resolution to AUTHORIZE travel reimbursement for the City Council Members who attend the Chambe',NULL,9,2,'Henley','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(208,'2023-02-21',1381,2800,'Resolution to APPROVE the School Board’s entry into an Interim Agreement re the design work at Pr','schools',8,1,'Schulman','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(209,'2023-02-21',1381,2798,'Amend and Repeal City Code Pertaining to Law Enforcement Officers',NULL,2,1,'Wilson','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(210,'2023-02-21',1381,2797,'MOA Between VBPD and VBSO',NULL,6,3,'Remick','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(211,'2023-02-21',1381,2763,'Amend City Code Pertaining to Animals on the Beach & Boardwalk',NULL,8,1,'Henley','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(212,'2023-02-07',1379,2750,'Contract for Community Engagement for City''s Election System',NULL,8,3,'Rouse','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(213,'2023-01-17',1377,2708,'ATLANTIC DEVELOPMENT ASSOCIATES, LLC & WPL VENTURES, LLC for a Variance to Section 4.4(b) of the','rezoning',7,4,'Dyer, Henley, Schulman','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(214,'2023-01-17',1377,2688,'Rescind Direction to Develop Public Input on VB Election System','development',5,6,'Berlucchi, Dyer, Henley, Wilson, Schulman','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(215,'2022-12-13',1369,2618,'Appropriate Funds in School Reversion Funding','budget',6,4,'Henley','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(216,'2022-12-06',1367,2608,'Vanguard Landing',NULL,7,4,'Henley','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(217,'2022-12-06',1367,2586,'Temp Encroachment - 2304 Windward Shore Drive','rezoning',10,1,'','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(218,'2022-12-06',1367,2556,'SITW Sponsorship Agreement',NULL,10,1,'','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(219,'2022-12-06',1367,1998,'Noise Ordinance',NULL,5,6,'Dyer, Wilson','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(220,'2022-11-15',1352,2554,'Charter Amendment - Filling Vacancies for City Council and Mayor','governance',6,5,'Dyer, Henley, Wilson','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(221,'2022-09-20',1359,2393,'$7,194,674 FY 2022 to FY 2023 Carry Forward',NULL,8,2,'','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(222,'2022-09-20',1359,2301,'Dam Neck Associates, LLC','defense',9,1,'','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(223,'2022-09-06',1336,2388,'TFJG CANOPY, LLC / SCHOOL BOARD OF THE CITY OF VIRGINIA BEACH','schools',7,1,'','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(224,'2022-09-06',1336,2061,'Proposed Disposable Plastic Bag Tax',NULL,8,1,'','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(225,'2022-08-16',1335,2331,'Atlantic Park Performance Grant Exhibit ''A'' Amendment','schools',8,3,'','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(226,'2022-07-25',1308,2216,'TEST Planning: Longcreek, LLC for a Conditional Use Permit','rezoning',5,4,'Berlucchi, Dyer, Wilson','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(227,'2022-07-14',1292,2163,'TEST Planning: Longcreek, LLC for a Conditional Use Permit','rezoning',2,1,'Henley','Deferred','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(228,'2022-05-03',1199,1952,'TEST: Planning: Resolution to Adopt and Amend the Virginia Beach Comprehensive Plan 2016','rezoning',9,2,'','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(229,'2022-05-03',1199,1950,'TEST: Ordinance to Accept and Approve: VDH, EMS',NULL,9,2,'','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(230,'2022-05-03',1199,1949,'TEST: Ordinances to Accept and Appropriate: Firehouse Subs Public Safety Foundation','budget',9,2,'','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(231,'2022-05-03',1199,1948,'TEST: Ordinance to Approve the sale of School Board Property','schools',9,2,'','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(232,'2022-05-03',1199,1947,'TEST: Resolution to Authroize the issuance of Revenue Bonds',NULL,9,2,'','Vote Complete','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(233,'2021-12-05',1140,1839,'TEST AGENDA ITEM 1/13/2022',NULL,2,3,'Henley','Defeated','2026-06-24T00:39:47.699484+00:00');
INSERT INTO "vb_split_votes" VALUES(234,'2021-12-05',1140,1777,'9/24/2021 Test Legislative Item',NULL,3,1,'Henley','Defeated','2026-06-24T00:39:47.699484+00:00');
COMMIT;
