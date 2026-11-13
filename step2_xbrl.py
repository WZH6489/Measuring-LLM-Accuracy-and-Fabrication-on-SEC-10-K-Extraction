import requests

headers = {"User-Agent": "William Huang wzh1@sfu.ca"}  # <-- put your real name and email
url = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"  # Apple

facts = requests.get(url, headers=headers).json()
usgaap = facts["facts"]["us-gaap"]

for tag in ["NetIncomeLoss", "Assets", "RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "OperatingIncomeLoss"]:
    print("\n==", tag)
    if tag not in usgaap:
        print("  (Apple doesn't use this tag)")
        continue
    rows = usgaap[tag]["units"]["USD"]
    annual = [r for r in rows if r["form"] == "10-K"]
    for r in annual[-4:]:
        print(" ", r["fy"], "period end:", r["end"], "value:", r["val"])
