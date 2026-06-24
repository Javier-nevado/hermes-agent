---
name: m365-graph-api
description: "Microsoft 365 Graph API — mail, calendar, tasks, Teams, chats, Drive, contacts, Planner. MSAL device code flow."
version: 5.0.0
tags: [m365, graph-api, mail, calendar, teams, tasks, drive, contacts, planner, msal]
---

# M365 Graph API

Microsoft 365 via Graph API using MSAL Python.

## Files

- **Credentials**: `workspace/agent/credentials/m365.json` — contains `client_id`, `tenant_id`, `user_email`
- **Token cache**: `workspace/agent/credentials/m365-token-cache.json` — created after auth, DO NOT EDIT
- **Temp flow**: `workspace/agent/credentials/m365-pending-flow.json` — temp file, auto-deleted after auth

---

## TENANT SETUP — For Customer Admins

When onboarding a new customer, the customer's Microsoft 365 admin must create an App Registration in their Azure AD tenant. Provide them the PowerShell script below.

**Prerequisites (tell the admin):**
1. Windows PowerShell 5.1 or PowerShell 7+
2. Microsoft Graph PowerShell module: `Install-Module Microsoft.Graph -Scope CurrentUser`
3. Global Administrator or Application Administrator role in the M365 tenant

**Steps:**
1. Send the customer the script from "PowerShell Setup Script" below
2. The admin runs it — it creates the app, adds permissions, enables device code flow, grants admin consent
3. The script outputs `client_id` and `tenant_id`
4. The admin sends you these two values
5. You create `workspace/agent/credentials/m365.json` with those values:
```python
import json, os
creds = {
    "client_id": "<from admin>",
    "tenant_id": "<from admin>",
    "user_email": "<customer's M365 email>"
}
os.makedirs("workspace/agent/credentials", exist_ok=True)
with open("workspace/agent/credentials/m365.json", "w") as f:
    json.dump(creds, f, indent=2)
print("Credentials saved. Proceed to authentication (Call A + Call B).")
```

### PowerShell Setup Script

Give this script to the customer's M365 admin. They run it in PowerShell as a Global Admin.

```powershell
<#
.SYNOPSIS
    Creates an Azure AD App Registration for ABI Agent M365 integration.
.DESCRIPTION
    This script creates a public client app registration with delegated Microsoft Graph
    permissions for mail, calendar, tasks, Teams, OneDrive, and contacts access.
    It uses device code flow (no client secret needed).
    Run as Global Administrator or Application Administrator.
.EXAMPLE
    .\New-AbiM365AppRegistration.ps1 -AppName "Contoso - ABI Agent"
#>

param(
    [string]$AppName = "ABI Agent - M365 Integration"
)

# --- 1. Install & Import ---
if (-not (Get-Module -ListAvailable -Name Microsoft.Graph*)) {
    Write-Host "Installing Microsoft Graph PowerShell module..." -ForegroundColor Yellow
    Install-Module Microsoft.Graph -Scope CurrentUser -Force
}

Import-Module Microsoft.Graph

# --- 2. Connect as admin ---
Write-Host "`nConnecting to Microsoft Graph (sign in as Global Admin)..." -ForegroundColor Cyan
Connect-MgGraph -Scopes "Application.ReadWrite.All","Directory.ReadWrite.All","AppRoleAssignment.ReadWrite.All" -NoWelcome

# --- 3. Get Microsoft Graph service principal ---
$graphSp = Get-MgServicePrincipal -Filter "appId eq '00000003-0000-0000-c000-000000000000'" -ErrorAction Stop
if (-not $graphSp) {
    Write-Error "Microsoft Graph service principal not found. Is this an M365 tenant?"
    exit 1
}

# --- 4. Define delegated permissions ---
$permissions = @(
    "Mail.Read"
    "Mail.Send"
    "Calendars.Read"
    "User.Read"
    "Chat.Read"
    "Chat.ReadWrite"
    "Files.Read.All"
    "Files.ReadWrite.All"
    "Tasks.Read"
    "Tasks.ReadWrite"
    "Contacts.Read"
    "ChannelMessage.Send"
    "Team.ReadBasic.All"
    "Channel.ReadBasic.All"
    "Sites.Read.All"
    "Group.Read.All"
)

# --- 5. Resolve permission IDs ---
Write-Host "Resolving permission scopes..." -ForegroundColor Cyan
$resourceAccess = @()
$missingPerms = @()
foreach ($perm in $permissions) {
    $scope = $graphSp.Oauth2PermissionScopes | Where-Object { $_.Value -eq $perm }
    if ($scope) {
        $resourceAccess += @{
            Id   = $scope.Id
            Type = "Scope"  # Scope = delegated permission
        }
    } else {
        $missingPerms += $perm
    }
}

if ($missingPerms.Count -gt 0) {
    Write-Warning "These permissions were NOT found in the tenant: $($missingPerms -join ', ')"
}

# --- 6. Check if app already exists ---
$existingApp = Get-MgApplication -Filter "displayName eq '$AppName'" -ErrorAction SilentlyContinue
if ($existingApp) {
    Write-Host "App '$AppName' already exists (AppId: $($existingApp.AppId)). Updating permissions..." -ForegroundColor Yellow
    $app = $existingApp
    Update-MgApplication -ApplicationId $app.Id -RequiredResourceAccess @(
        @{
            ResourceAppId  = "00000003-0000-0000-c000-000000000000"
            ResourceAccess = $resourceAccess
        }
    )
} else {
    Write-Host "Creating app registration '$AppName'..." -ForegroundColor Cyan
    $app = New-MgApplication -DisplayName $AppName -SignInAudience "AzureADMyOrg" -RequiredResourceAccess @(
        @{
            ResourceAppId  = "00000003-0000-0000-c000-000000000000"
            ResourceAccess = $resourceAccess
        }
    )
}

# --- 7. Enable public client flows (device code flow) ---
Update-MgApplication -ApplicationId $app.Id -IsFallbackPublicClient $true
Write-Host "Device code flow enabled." -ForegroundColor Green

# --- 8. Create service principal (enterprise app) ---
$sp = Get-MgServicePrincipal -Filter "appId eq '$($app.AppId)'" -ErrorAction SilentlyContinue
if (-not $sp) {
    $sp = New-MgServicePrincipal -AppId $app.AppId
    Write-Host "Service principal created." -ForegroundColor Green
}

# --- 9. Grant admin consent for delegated permissions ---
# Build space-separated scope string for the OAuth2 permission grant
$scopeString = ($permissions | Where-Object { $_ -notin $missingPerms }) -join " "

# Remove existing grants if any
$existingGrants = Get-MgServicePrincipalOauth2PermissionGrant -ServicePrincipalId $sp.Id -ErrorAction SilentlyContinue
if ($existingGrants) {
    foreach ($grant in $existingGrants) {
        Remove-MgOauth2PermissionGrant -OAuth2PermissionGrantId $grant.Id -ErrorAction SilentlyContinue
    }
}

# Create new grant
$body = @{
    clientId    = $sp.Id
    consentType = "AllPrincipals"
    resourceId  = $graphSp.Id
    scope       = $scopeString
}
$grantUrl = "$((Get-MgContext).Environment.GraphEndpoint)/v1.0/oauth2PermissionGrants"
try {
    Invoke-MgGraphRequest -Method POST -Uri $grantUrl -Body $body -ErrorAction Stop
    Write-Host "Admin consent granted for all delegated permissions." -ForegroundColor Green
} catch {
    Write-Warning "Auto-consent failed: $($_.Exception.Message)"
    Write-Host "You may need to grant admin consent manually:" -ForegroundColor Yellow
    Write-Host "  https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationMenuBlade/~/CallAnAPI/appId/$($app.AppId)" -ForegroundColor Yellow
}

# --- 10. Output result ---
$tenantId = (Get-MgContext).TenantId

Write-Host ""
Write-Host "=============================================" -ForegroundColor Green
Write-Host "  App Registration Complete!" -ForegroundColor Green
Write-Host "=============================================" -ForegroundColor Green
Write-Host ""
Write-Host "  App Name : $AppName"
Write-Host "  client_id: $($app.AppId)"
Write-Host "  tenant_id: $tenantId"
Write-Host ""
Write-Host "Send the client_id and tenant_id to your ABI provider." -ForegroundColor Cyan
Write-Host ""

# Disconnect
Disconnect-MgGraph | Out-Null
```

### What the Script Does

| Step | Action |
|------|--------|
| 1 | Installs Microsoft Graph PowerShell module if missing |
| 2 | Connects as Global Admin (browser login) |
| 3 | Looks up the Microsoft Graph service principal |
| 4 | Resolves all 16 delegated permission IDs |
| 5 | Creates the app registration (or updates if exists) |
| 6 | Enables device code flow (`isFallbackPublicClient = true`) |
| 7 | Creates the service principal (enterprise app) |
| 8 | Grants admin consent for all delegated permissions |
| 9 | Outputs `client_id` and `tenant_id` |

### Required Permissions (for reference)

| Permission | Purpose |
|-----------|---------|
| Mail.Read | Read mailbox |
| Mail.Send | Send emails |
| Calendars.Read | Read calendar events |
| User.Read | Read user profile |
| Chat.Read | Read Teams chats |
| Chat.ReadWrite | Send Teams chat messages |
| Files.Read.All | Read OneDrive/SharePoint files |
| Files.ReadWrite.All | Upload/edit OneDrive/SharePoint files |
| Tasks.Read | Read To Do tasks |
| Tasks.ReadWrite | Create/complete To Do tasks |
| Contacts.Read | Read contacts |
| ChannelMessage.Send | Send Teams channel messages |
| Team.ReadBasic.All | List joined teams |
| Channel.ReadBasic.All | List team channels |
| Sites.Read.All | Read SharePoint sites |
| Group.Read.All | Read M365 groups |

---

## AUTHENTICATION

### CRITICAL RULES — READ BEFORE PROCEEDING

1. Authentication uses TWO SEPARATE `execute_code` calls — Call A and Call B
2. Call A ONLY starts the flow and prints a URL+code. It does NOT wait for the user.
3. You MUST show the URL+code to the user and wait for them to say "done" BEFORE running Call B.
4. Call B loads the saved flow and exchanges it for a token.
5. NEVER combine Call A and Call B into one execute_code — the sandbox will time out.
6. NEVER modify `m365.json` — the flow goes in `m365-pending-flow.json`, the token goes in `m365-token-cache.json`.

---

### Call A — Start device flow (takes 1 second, then WAIT for user)

Run this code EXACTLY as-is. Do NOT add acquire_token_by_device_flow to this call.

```python
import json, os, msal

with open("workspace/agent/credentials/m365.json") as f:
    creds = json.load(f)

SCOPES = [
    "https://graph.microsoft.com/Mail.Read", "https://graph.microsoft.com/Mail.Send",
    "https://graph.microsoft.com/Calendars.Read", "https://graph.microsoft.com/User.Read",
    "https://graph.microsoft.com/Chat.Read", "https://graph.microsoft.com/Chat.ReadWrite",
    "https://graph.microsoft.com/Files.Read.All", "https://graph.microsoft.com/Files.ReadWrite.All",
    "https://graph.microsoft.com/Tasks.Read", "https://graph.microsoft.com/Tasks.ReadWrite",
    "https://graph.microsoft.com/Contacts.Read",
    "https://graph.microsoft.com/ChannelMessage.Send",
    "https://graph.microsoft.com/Team.ReadBasic.All",
    "https://graph.microsoft.com/Channel.ReadBasic.All",
    "https://graph.microsoft.com/Sites.Read.All",
    "https://graph.microsoft.com/Group.Read.All",
]

authority = f"https://login.microsoftonline.com/{creds['tenant_id']}"
app = msal.PublicClientApplication(creds["client_id"], authority=authority)
flow = app.initiate_device_flow(scopes=SCOPES)

# Save flow to SEPARATE temp file (NOT m365.json)
flow_path = "workspace/agent/credentials/m365-pending-flow.json"
os.makedirs(os.path.dirname(flow_path), exist_ok=True)
with open(flow_path, "w") as f:
    json.dump(flow, f)

# Print instructions for user — STOP HERE and wait
print(f"Open this URL: {flow['verification_uri']}")
print(f"Enter this code: {flow['user_code']}")
print("STOP: Ask the user to authenticate, then run Call B after they confirm.")
```

**AFTER this call completes:** Tell the user the URL and code. **STOP AND WAIT.** Do NOT proceed until the user confirms.

---

### Call B — Complete auth (run ONLY after user says "done")

```python
import json, os, msal

with open("workspace/agent/credentials/m365.json") as f:
    creds = json.load(f)

flow_path = "workspace/agent/credentials/m365-pending-flow.json"
cache_path = "workspace/agent/credentials/m365-token-cache.json"

# Load the flow saved by Call A
with open(flow_path) as f:
    flow = json.load(f)

authority = f"https://login.microsoftonline.com/{creds['tenant_id']}"
app = msal.PublicClientApplication(creds["client_id"], authority=authority)

# Exchange the flow for a token (user already authenticated in browser)
result = app.acquire_token_by_device_flow(flow)

if "access_token" in result:
    # Save the token cache as raw MSAL cache JSON
    cache_raw = app.token_cache.serialize()
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w") as f:
        f.write(cache_raw)
    # Clean up temp flow file
    os.remove(flow_path)
    print(f"SUCCESS: Authenticated. Token length: {len(result['access_token'])}")
else:
    err = result.get("error_description", str(result))[:300]
    print(f"FAILED: {err}")
    print("Try again from Call A.")
```

**AFTER this call:** If SUCCESS, you can now run any operation below. If FAILED, restart from Call A.

---

## Operations — run after successful auth

Every operation needs a token. Include this helper at the TOP of each operation:

```python
import json, os, requests, msal

with open("workspace/agent/credentials/m365.json") as f:
    CREDS = json.load(f)
USER = CREDS.get("user_email", "me")
GRAPH = "https://graph.microsoft.com/v1.0"
CACHE_PATH = "workspace/agent/credentials/m365-token-cache.json"

def get_token():
    if not os.path.exists(CACHE_PATH):
        print("ERROR: No token. Run auth Call A then Call B first.")
        return None
    cache = msal.SerializableTokenCache()
    with open(CACHE_PATH) as f:
        cache.deserialize(f.read())
    app = msal.PublicClientApplication(
        CREDS["client_id"],
        authority=f"https://login.microsoftonline.com/{CREDS['tenant_id']}",
        token_cache=cache,
    )
    accounts = app.get_accounts()
    if not accounts:
        print("ERROR: No accounts in cache. Re-run auth.")
        return None
    result = app.acquire_token_silent(["https://graph.microsoft.com/.default"], account=accounts[0])
    if result and "access_token" in result:
        if cache.has_state_changed:
            with open(CACHE_PATH, "w") as f:
                f.write(cache.serialize())
        return result["access_token"]
    print("ERROR: Token expired. Re-run auth Call A then Call B.")
    return None
```

Then use `get_token()` and make direct requests. Example:

```python
token = get_token()
if not token:
    pass  # stop here
else:
    r = requests.get(f"{GRAPH}/users/{USER}/mailFolders/Inbox/messages",
        headers={"Authorization": f"Bearer {token}"},
        params={"$top": 10, "$orderby": "receivedDateTime desc"}, timeout=15)
    if r.status_code == 200:
        for msg in r.json().get("value", []):
            print(f"[{msg['receivedDateTime'][:16]}] {msg['subject']}")
    else:
        print(f"HTTP {r.status_code}: {r.text[:200]}")
```

### Available Operations

Use the same pattern (helper + get_token + requests) for all:

1. **Read Mail**: `GET /users/{USER}/mailFolders/{folder}/messages?$top=10&$orderby=receivedDateTime desc`
2. **Send Mail**: `POST /users/{USER}/sendMail` with `{"message": {"subject": "...", "body": {...}, "toRecipients": [...]}}`
3. **Calendar**: `GET /users/{USER}/calendarView?startDateTime=...&endDateTime=...`
4. **Tasks**: `GET /users/{USER}/todo/lists` then `GET /users/{USER}/todo/lists/{id}/tasks`
5. **Teams**: `GET /me/joinedTeams`
6. **Channels**: `GET /teams/{team_id}/channels`
7. **Chats**: `GET /me/chats?$top=20`
8. **Chat Message**: `POST /chats/{chat_id}/messages` with `{"body": {"content": "..."}}`
9. **Drive Files**: `GET /me/drive/root/children?$top=20`
10. **Upload File**: `PUT /me/drive/root:/{path}:/content` with file bytes
11. **Contacts**: `GET /me/contacts?$top=20`
12. **Planner**: `GET /me/planner/tasks?$top=20` with header `Prefer: return=representation`

---

## Troubleshooting

- **"No token"**: Run auth Call A, wait for user to authenticate, then run Call B.
- **HTTP 401**: Token expired. Re-run auth (Call A + Call B).
- **"AADSTS70016"**: User didn't auth in time. Restart from Call A.
- **"AADSTS700027"**: Client assertion failed. App registration may need device code flow enabled — re-run the PowerShell script.
- **"AADSTS65001"**: User or admin has not consented. Admin must run the PowerShell script or consent at the Azure portal.
- **Do NOT call login(), complete_auth(), or any MCP tool** — those no longer exist.
- **Do NOT combine Call A and Call B** — the sandbox will kill the process.
- **Do NOT modify m365.json** — it only contains client_id, tenant_id, user_email.
