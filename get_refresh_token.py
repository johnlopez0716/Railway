"""
Run this ONCE per Gmail account (4 times total) on your own computer.
It opens a browser, you log into that specific Gmail account, approve access,
and it prints a refresh token to paste into Railway's environment variables.

Setup before running (one-time, covers all 4 accounts):
1. Go to console.cloud.google.com, create a project (or use an existing one).
2. Enable the "Gmail API" for that project.
3. Go to APIs & Services -> Credentials -> Create Credentials -> OAuth client ID.
   - Application type: Desktop app
   - Download the JSON file, save it as credentials.json in this same folder.
4. pip install google-auth-oauthlib

Then run: python get_refresh_token.py
Log in with the Gmail account you want a token for. Repeat 4 times, once per
inbox, logging into a different account's browser session each time (use an
incognito window if your browser stays logged into the wrong account).
"""

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
creds = flow.run_local_server(port=0)

print("\n\n=== COPY THIS REFRESH TOKEN ===")
print(creds.refresh_token)
print("=== paste it into the matching GMAIL_ACCOUNT_n_REFRESH_TOKEN variable in Railway ===\n")
