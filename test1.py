import os
import sys
import requests
import json
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("INSTA_API_KEY") or sys.exit("ERROR: INSTA_API_KEY is not set (add it to .env).")

url = "https://instafinancials.com/api/InstaSummary/v1/json/CompanyCIN/L32309KA1954GOI000787"
request_headers = {
    "user-key": api_key
}

response = requests.get(url, headers=request_headers)
print(response.status_code)        # 200 = success

data = response.json()             # parsed JSON -> Python dict
print(json.dumps(data, indent=2))  # pretty-print (import json)

# Access fields per the sample JSON structure, e.g.:
# data["InstaSummary"]["CompanyMasterSummary"]["CompanyName"]