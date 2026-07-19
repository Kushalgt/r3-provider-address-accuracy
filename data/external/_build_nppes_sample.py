"""Reconstruct the 30-NPI NPPES sample cache from the values fetched via the
NPPES MCP tool in-session. Emits data/external/nppes_sample.json in the exact
shape produced by nppes_enrich.save_cache() ({npi: results[0]-object or null}),
storing only the fields parse_nppes_result() consumes. Faithful to a live run
for the demo/validation pass; the user regenerates the full cache with
`build_enriched_dataset.py --nppes-live --save-cache ...` where there is internet.
"""
import json, os

def rec(enum, status, credential, last_updated, tax_code, tax_desc, addresses, practice_locations):
    return {
        "number": None,
        "enumeration_type": enum,
        "basic": {"status": status, "credential": credential, "last_updated": last_updated},
        "taxonomies": [{"code": tax_code, "desc": tax_desc, "primary": True}],
        "addresses": [
            {"address_purpose": p, "address_1": a1, "postal_code": z} for (p, a1, z) in addresses
        ],
        "practiceLocations": [
            {"address_1": a1, "postal_code": z} for (a1, z) in practice_locations
        ],
    }

C = {}
C["1083291587"] = rec("NPI-1","A","MD","2024-07-02","208000000X","Pediatrics",
    [("MAILING","1964 W 11 MILE RD","480723046"),("LOCATION","13500 E MCNICHOLS RD","482053426")],
    [("1964 W 11 MILE RD","480723046")])
C["1063067304"] = rec("NPI-1","A","PsyD","2025-05-14","103TC0700X","Psychologist, Clinical",
    [("LOCATION","4211 PARKWAY PLACE DR SW","494182695"),("MAILING","300 68TH ST SE","495486927")],
    [("300 68TH ST SE","495486927"),("6500 BYRON CENTER AVE SW","493159080")])
C["1639836174"] = rec("NPI-1","A",None,"2024-09-17","363LF0000X","Nurse Practitioner, Family",
    [("LOCATION","4701 TOWNE CENTRE RD","486042834"),("MAILING","6273 PADDOCK LN","486032734")],
    [])
C["1033397518"] = rec("NPI-1","A","Ph.D.","2024-12-17","103TC0700X","Psychologist, Clinical",
    [("MAILING","30400 TELEGRAPH RD","480254537"),("LOCATION","30400 TELEGRAPH RD","480254537")],
    [])
C["1275503690"] = rec("NPI-1","A","M.D.","2024-03-05","207RE0101X","Internal Medicine, Endocrinology, Diabetes & Metabolism",
    [("LOCATION","6304 USA HEALTH BLVD","366080020"),("MAILING","PO BOX 21595","049154112")],
    [])
C["1023577962"] = rec("NPI-1","A","DO","2022-08-18","207R00000X","Internal Medicine",
    [("MAILING","PO BOX 517","359570517"),("LOCATION","2692 US HIGHWAY 431","359575845")],
    [])
C["1275952517"] = rec("NPI-1","A","MD","2026-02-19","208000000X","Pediatrics",
    [("LOCATION","350 SPRINGVILLE STA","351466163"),("MAILING","350 SPRINGVILLE STA","351466163")],
    [("2332 GALIANO ST","331345402")])
C["1356330054"] = rec("NPI-1","A","MD","2026-02-04","207RC0000X","Internal Medicine, Cardiovascular Disease",
    [("LOCATION","3980 COLONNADE PKWY","352432382"),("MAILING","PO BOX 11407, DEPARTMENT 8007","352468007")],
    [])
C["1093715369"] = rec("NPI-1","A","M.D.","2025-08-18","207RX0202X","Internal Medicine, Medical Oncology",
    [("LOCATION","1200 US HIGHWAY 22 3RD FL","088072943"),("MAILING","629 CRANBURY RD FL 2","088164096")],
    [])
C["1023215217"] = rec("NPI-1","A","M.D.","2016-03-31","208000000X","Pediatrics",
    [("MAILING","100 E PENN SQ","191073323"),("LOCATION","100 E PENN SQ","191073323")],
    [])
C["1053479246"] = rec("NPI-1","A","MD","2011-01-13","208000000X","Pediatrics",
    [("LOCATION","600 MARLTON PIKE W","080023598"),("MAILING","402 LIPPINCOTT DR","080534112")],
    [])
C["1053524777"] = rec("NPI-1","A","MD","2007-07-08","208000000X","Pediatrics",
    [("MAILING","2500 LEMOINE AVE","07024"),("LOCATION","2500 LEMOINE AVE","07024")],
    [])
C["1174783336"] = rec("NPI-1","A","MD","2021-11-19","207RI0011X","Internal Medicine, Interventional Cardiology",
    [("LOCATION","161 FORT WASHINGTON AVE FL 6","100323729"),("MAILING","161 FORT WASHINGTON AVE FL 6","100323729")],
    [])
C["1205425204"] = rec("NPI-1","A","APN","2025-10-22","363LF0000X","Nurse Practitioner, Family",
    [("MAILING","354 HURFFVILLE CROSSKEYS RD BLDG 2","080803550"),("LOCATION","354 HURFFVILLE CROSSKEYS RD BLDG 2","080803550")],
    [])
C["1699857854"] = None  # NPI valid but unassigned in NPPES
C["1417191917"] = rec("NPI-1","A","M.D.","2022-07-21","207N00000X","Dermatology",
    [("LOCATION","347 MOUNT PLEASANT AVE STE 103","070522745"),("MAILING","7150 GREENVILLE AVE","752315165")],
    [("7150 GREENVILLE AVE","752317900")])
C["1184602682"] = rec("NPI-1","A","M.D.","2020-06-01","207R00000X","Internal Medicine",
    [("LOCATION","1707 W CHARLESTON BLVD","891022351"),("MAILING","3016 W CHARLESTON BLVD STE 100","891021973")],
    [])
C["1437800455"] = rec("NPI-1","A","LMFT, LCADC","2026-04-13","106H00000X","Marriage & Family Therapist",
    [("MAILING","27436 BIG BEND DR","925858167"),("LOCATION","2701 N TENAYA WAY STE 200","891280480")],
    [])
C["1659876282"] = rec("NPI-1","A","MD","2024-10-30","207R00000X","Internal Medicine",
    [("LOCATION","3850 W NEVSO DR UNIT 385","891034072"),("MAILING","3850 W NEVSO DR UNIT 385","891034072")],
    [("1800 W CHARLESTON BLVD","891022386")])
C["1841991049"] = rec("NPI-1","A","FNP-C","2025-10-29","363LF0000X","Nurse Practitioner, Family",
    [("MAILING","6675 S TENAYA WAY STE 200","891131932"),("LOCATION","6675 S TENAYA WAY STE 200","891131932")],
    [])
C["1265958821"] = rec("NPI-1","A",None,"2021-06-15","367500000X","Nurse Anesthetist, Certified Registered",
    [("LOCATION","1400 LOCUST ST","152195114"),("MAILING","5508 LAKESIDE DR","150449253")],
    [])
C["1184381998"] = rec("NPI-1","A","LISW -S","2025-07-23","1041C0700X","Social Worker, Clinical",
    [("LOCATION","895 CENTRAL AVE","452021961"),("MAILING","895 CENTRAL AVE STE 300","452021984")],
    [])
C["1912425075"] = rec("NPI-1","A","LPCC-S, PhD","2025-10-29","101YM0800X","Counselor, Mental Health",
    [("MAILING","796 LILLY LN","452452510"),("LOCATION","4030 MOUNT CARMEL TOBASCO RD","452553400")],
    [("796 LILLY LN","452452510")])
C["1679246078"] = rec("NPI-1","A","BCBA","2025-09-15","103K00000X","Behavior Analyst",
    [("MAILING","7500 SAN FELIPE ST STE 990","770631708"),("LOCATION","2785 SOM CENTER RD","440946501")],
    [("20575 CENTER RIDGE RD STE 400","441163422")])
C["1194464693"] = rec("NPI-1","A",None,"2023-03-17","363LP0808X","Nurse Practitioner, Psych/Mental Health",
    [("LOCATION","17075 DEVONSHIRE ST","913251600"),("MAILING","17221 ROSCOE BLVD UNIT 4","913254031")],
    [("1225 W 190TH ST STE 280","902484305")])
C["1891851861"] = rec("NPI-1","A","MD","2010-07-30","207Q00000X","Family Medicine",
    [("MAILING","231 W VERNON AVE","900372700"),("LOCATION","231 W VERNON AVE","900372700")],
    [])
C["1326068354"] = rec("NPI-1","A","M.D.","2012-10-19","208000000X","Pediatrics",
    [("MAILING","3111 W BEVERLY BLVD","906402216"),("LOCATION","3111 W BEVERLY BLVD","906402216")],
    [])
C["1811260474"] = rec("NPI-1","A","BCBA","2012-02-13","103K00000X","Behavior Analyst",
    [("MAILING","6059 BRISTOL PKWY","902306663"),("LOCATION","6059 BRISTOL PKWY","902306663")],
    [])
C["1154955383"] = rec("NPI-1","A","MA, LPC","2020-02-26","101YP2500X","Counselor, Professional",
    [("MAILING","2939 W WOODLAWN AVE","782285015"),("LOCATION","2939 W WOODLAWN AVE","782285015")],
    [])
C["1033662945"] = rec("NPI-1","A","CRNA","2019-04-23","367500000X","Nurse Anesthetist, Certified Registered",
    [("MAILING","2401 S 31ST ST","765080001"),("LOCATION","2401 S 31ST ST","76508")],
    [("1500 CITYWEST BLVD","77042")])

# stamp the true NPI into each record's `number` field
for k, v in C.items():
    if v is not None:
        v["number"] = k

out = os.path.join(os.path.dirname(__file__), "nppes_sample.json")
with open(out, "w") as fh:
    json.dump(C, fh, indent=1)
print(f"wrote {len(C)} NPIs ({sum(v is not None for v in C.values())} found) -> {out}")
