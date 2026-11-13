import requests, os

headers = {"User-Agent": "William Huang wzh1@sfu.ca"}
cik = "0000320193"  # Apple

sub = requests.get(f"https://data.sec.gov/submissions/CIK{cik}.json", headers=headers).json()
recent = sub["filings"]["recent"]

# the lists are newest first, so the first 10-K is the latest
for i, form in enumerate(recent["form"]):
    if form == "10-K":
        accn = recent["accessionNumber"][i]
        doc = recent["primaryDocument"][i]
        print("accession:", accn)
        print("period end:", recent["reportDate"][i], "| filed:", recent["filingDate"][i])
        break

url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn.replace('-', '')}/{doc}"
print("url:", url)

os.makedirs("data", exist_ok=True)
html = requests.get(url, headers=headers).text
with open("data/apple_10k.html", "w") as f:
    f.write(html)
print(len(html), "characters saved")
