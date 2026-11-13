import json, re, os
import anthropic
from bs4 import BeautifulSoup

MODEL = "claude-haiku-5-5"  # cheap model for development

# answer key (USD); revenue and net income came from XBRL, operating income from the filing page for now
KEY = {
    "revenue": 478834000000,
    "net_income": 132175000000,
    "operating_income": 158063000000,
}

# 1. turn the filing HTML into text and find the income statement
html = open("data/apple_10k.html").read()
text = BeautifulSoup(html, "html.parser").get_text("\n")
text = re.sub(r"\n\s*\n+", "\n", text)

section = None
for m in re.finditer(r"CONSOLIDATED STATEMENTS OF OPERATIONS", text):
    chunk = text[m.start(): m.start() + 4000]
    if "Total net sales" in chunk and "Operating income" in chunk:
        section = chunk
        break
assert section, "could not find the income statement"

# 2. ask the model
prompt = f"""Extract these fields for the most recent fiscal year from the text below.
Return JSON only. For each field give: value (a number in full USD, applying any
scale stated in the header), unit, period, and quote (a verbatim quote from the text).
Fields: revenue, net_income, operating_income

{section}"""

client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
resp = client.messages.create(
    model=MODEL, max_tokens=4000,
    messages=[{"role": "user", "content": prompt}],
)

# keep only the text blocks (the reply may start with a thinking block)
raw = "".join(b.text for b in resp.content if b.type == "text")
print("tokens:", resp.usage.input_tokens, "in,", resp.usage.output_tokens, "out")

# 3. save the raw response
os.makedirs("results", exist_ok=True)
with open("results/apple_haiku_raw.txt", "w") as f:
    f.write(raw)

answer = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])

# 4. score it
print(f"model: {MODEL}\n")
for field, truth in KEY.items():
    got = answer[field]["value"]
    ok = abs(got - truth) / truth <= 0.01
    print(f"{field:17} got {got:>15,} | key {truth:>15,} | {'MATCH' if ok else 'WRONG'}")
