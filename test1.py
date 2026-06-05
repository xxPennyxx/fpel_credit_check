import requests
import json

url = "https://instafinancials.com/api/InstaSummary/v1/json/CompanyCIN/L32309KA1954GOI000787"
request_headers = {
    "user-key": "fm3IoYaPDjL2U09KYZlsdo0zp6EGUHUe30cBFTGs2TGN8jnG1wm8dQ=="
}

response = requests.get(url, headers=request_headers)
print(response.status_code)        # 200 = success

data = response.json()             # parsed JSON -> Python dict
print(json.dumps(data, indent=2))  # pretty-print (import json)

# Access fields per the sample JSON structure, e.g.:
# data["InstaSummary"]["CompanyMasterSummary"]["CompanyName"]