# Run this script on your local machine to push files to GitHub
# This supports interactive input for your GitHub token

param(
    [string]$Token = ""
)

$extractPath = "C:\Temp\customer-ops-github"

# Get file contents
$procfile = Get-Content "$extractPath\Procfile" -Raw
$runtime = Get-Content "$extractPath\runtime.txt" -Raw
$requirements = Get-Content "$extractPath\requirements.txt" -Raw
$wsgi = Get-Content "$extractPath\wsgi.py" -Raw

Write-Host ""
Write-Host "======================================" -ForegroundColor Cyan
Write-Host "GitHub Deployment - Push to Repo Root" -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "Files ready to push:" -ForegroundColor Green
Write-Host "  ✓ Procfile ($($procfile.Length) bytes)"
Write-Host "  ✓ runtime.txt ($($runtime.Length) bytes)"
Write-Host "  ✓ requirements.txt ($($requirements.Length) bytes)"
Write-Host "  ✓ wsgi.py ($($wsgi.Length) bytes)"
Write-Host ""

if (-not $Token) {
    Write-Host "Get your GitHub personal access token:" -ForegroundColor Yellow
    Write-Host "  1. Go to: https://github.com/settings/tokens" -ForegroundColor Gray
    Write-Host "  2. Click on 'youcc-pm' token (or create a new one with 'repo' scope)" -ForegroundColor Gray
    Write-Host "  3. Click 'Regenerate token'" -ForegroundColor Gray
    Write-Host "  4. Copy the token (it only displays once!)" -ForegroundColor Gray
    Write-Host ""
    $Token = Read-Host "Paste your GitHub token"
}

if ($Token -and $Token.Length -gt 10) {
    Write-Host ""
    Write-Host "Token received. Pushing files to GitHub..." -ForegroundColor Green
    Write-Host ""
    
    $baseUrl = "https://api.github.com/repos/TzahiAnidgar/customer-ops/contents"
    $headers = @{
        "Authorization" = "Bearer $Token"
        "Accept" = "application/vnd.github.v3+json"
        "X-GitHub-Api-Version" = "2022-11-28"
    }
    
    # Function to push a file
    function Push-File($fileName, $content) {
        $url = "$baseUrl/$fileName"
        $encodedContent = [System.Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($content))
        $body = @{
            "message" = "Add $fileName for Railway deployment"
            "content" = $encodedContent
            "branch" = "main"
        } | ConvertTo-Json
        
        try {
            $response = Invoke-WebRequest -Uri $url -Method PUT -Headers $headers -Body $body -ContentType "application/json"
            if ($response.StatusCode -eq 201 -or $response.StatusCode -eq 200) {
                Write-Host "  ✓ $fileName" -ForegroundColor Green
                return $true
            }
        } catch {
            if ($_.Exception.Response.StatusCode -eq 422) {
                Write-Host "  ✓ $fileName (already exists)" -ForegroundColor Green
                return $true
            }
            Write-Host "  ✗ $fileName" -ForegroundColor Red
            Write-Host "     Status: $($_.Exception.Response.StatusCode)" -ForegroundColor Red
            return $false
        }
    }
    
    Push-File "Procfile" $procfile
    Push-File "runtime.txt" $runtime
    Push-File "requirements.txt" $requirements
    Push-File "wsgi.py" $wsgi
    
    Write-Host ""
    Write-Host "✓ Files pushed to GitHub!" -ForegroundColor Green
    Write-Host "  Repository: https://github.com/TzahiAnidgar/customer-ops" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Next: Go to Railway and connect this repo for deployment" -ForegroundColor Yellow
} else {
    Write-Host "✗ Token not provided or invalid" -ForegroundColor Red
    Write-Host "  Token must be at least 10 characters" -ForegroundColor Gray
}

Write-Host ""
