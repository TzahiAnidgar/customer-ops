# PowerShell script to deploy files to GitHub using the API
# This uses your existing youcc-pm token from GitHub settings

Write-Host "GitHub Repository Deployment Script" -ForegroundColor Cyan
Write-Host "====================================" -ForegroundColor Cyan
Write-Host ""

# Configuration
$OWNER = "TzahiAnidgar"
$REPO = "customer-ops"
$BASE_PATH = "C:\Users\ZivMovshowitz\OneDrive - YouCC Technologies\Documents\Microsoft Scout\customer-ops-clean"

Write-Host "Deployment target: $OWNER/$REPO" -ForegroundColor Green
Write-Host ""

# Get token - user will paste it
$TOKEN = Read-Host "Paste your GitHub personal access token"

if (-not $TOKEN) {
    Write-Host "Token is required!" -ForegroundColor Red
    exit 1
}

$BASE_URL = "https://api.github.com/repos/$OWNER/$REPO/contents"
$HEADERS = @{
    "Authorization" = "token $TOKEN"
    "Accept" = "application/vnd.github.v3+json"
}

# Files to upload
$FILES = @{
    "Procfile" = "Procfile"
    "runtime.txt" = "runtime.txt"
    "requirements.txt" = "requirements.txt"
    "wsgi.py" = "wsgi.py"
}

function Upload-File {
    param(
        [string]$LocalFile,
        [string]$RemotePath
    )
    
    $FilePath = Join-Path $BASE_PATH $LocalFile
    
    if (-not (Test-Path $FilePath)) {
        Write-Host "[SKIP] $LocalFile - not found" -ForegroundColor Yellow
        return $false
    }
    
    # Read and encode file
    $Content = [System.IO.File]::ReadAllBytes($FilePath)
    $Encoded = [Convert]::ToBase64String($Content)
    
    $URL = "$BASE_URL/$RemotePath"
    $Body = @{
        message = "Add $RemotePath"
        content = $Encoded
    } | ConvertTo-Json
    
    try {
        $Response = Invoke-WebRequest -Uri $URL -Method PUT -Headers $HEADERS -Body $Body -ContentType "application/json" -ErrorAction Stop
        
        if ($Response.StatusCode -eq 201 -or $Response.StatusCode -eq 200) {
            Write-Host "[OK] $RemotePath" -ForegroundColor Green
            return $true
        }
    }
    catch {
        if ($_.Exception.Response.StatusCode -eq 422) {
            Write-Host "[EXISTS] $RemotePath (already in repo)" -ForegroundColor Cyan
            return $true
        }
        Write-Host "[FAIL] $RemotePath - $($_.Exception.Message)" -ForegroundColor Red
        return $false
    }
}

Write-Host "Uploading files..." -ForegroundColor Cyan
Write-Host ""

$uploaded = 0
foreach ($LocalFile in $FILES.Keys) {
    $RemotePath = $FILES[$LocalFile]
    if (Upload-File -LocalFile $LocalFile -RemotePath $RemotePath) {
        $uploaded++
    }
}

Write-Host ""
Write-Host "Upload Summary: $uploaded/$($FILES.Count) files successful" -ForegroundColor Cyan
Write-Host ""

if ($uploaded -ge 3) {
    Write-Host "✓ Success! Files are now in GitHub." -ForegroundColor Green
    Write-Host ""
    Write-Host "Next step: Connect the repo to Railway" -ForegroundColor Green
    Write-Host "1. Go to: https://railway.com/project/af7dfb0e-758b-4b08-a750-e6dde3563dd5/service/8a96365c-7bd7-4dc3-9513-e7d8e5d4cdcc/settings"
    Write-Host "2. Click 'Connect Repo'" 
    Write-Host "3. Select 'TzahiAnidgar/customer-ops'"
    Write-Host "4. Click 'Deploy'"
} else {
    Write-Host "✗ Upload incomplete. Check the errors above." -ForegroundColor Red
}
