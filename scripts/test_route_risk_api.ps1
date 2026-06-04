Set-Location (Join-Path $PSScriptRoot "..")

Write-Host ""
Write-Host "============================================================"
Write-Host "ROUTE RISK API TEST"
Write-Host "============================================================"
Write-Host ""

# ============================================================
# ROUTE RISK ENGINE API TEST
# ============================================================
#
# Purpose:
# - Submit a route-risk job to FastAPI.
# - Include latitude and longitude for each route segment.
# - Save the returned job_id automatically.
# - Check job status without manual copying.
# - Fetch raw results without manual copying.
# - Fetch the clean user-facing route-risk summary.
# - Print readable JSON output.
#
# Requirements:
# - Docker Desktop must be running.
# - FastAPI must be running.
# - Redis must be running.
# - Celery worker must be running.
#
# Start the app first with:
#
#     .\scripts\start_dev.ps1
#
# Then run this script from the project root:
#
#     .\scripts\test_route_risk_api.ps1

$body = @{
    route_name = "Rexburg to Idaho Falls Coordinate Test Route"
    origin = "Rexburg, ID"
    destination = "Idaho Falls, ID"
    segments = @(
        @{
            label = "Rexburg to Rigby"

            # Approximate route analysis point near Rexburg / US-20.
            latitude = 43.7419
            longitude = -111.8464

            weather = @{
                temperature_f = 28
                wind_mph = 18
                condition = "snow"
                visibility_miles = 3
            }

            road_condition = "normal"
            is_night = $true
        },
        @{
            label = "Rigby to Idaho Falls"

            # Approximate route analysis point near Rigby / US-20.
            latitude = 43.5987
            longitude = -111.9716

            weather = @{
                temperature_f = 34
                wind_mph = 30
                condition = "cloudy"
                visibility_miles = 5
            }

            road_condition = "construction"
            is_night = $true
        }
    )
} | ConvertTo-Json -Depth 10

Write-Host "Submitting coordinate-enabled route-risk job..."
Write-Host ""

try {
    $response = Invoke-RestMethod `
        -Uri "http://localhost:8000/submit_route_risk_job" `
        -Method Post `
        -ContentType "application/json" `
        -Body $body
} catch {
    Write-Host "ERROR: Failed to submit route-risk job."
    Write-Host "Make sure FastAPI is running at http://localhost:8000"
    Write-Host ""
    Write-Host $_
    exit 1
}

Write-Host "Submit response:"
$response | ConvertTo-Json -Depth 10

$jobId = $response.job_id

Write-Host ""
Write-Host "Saved job ID automatically:"
Write-Host $jobId

Write-Host ""
Write-Host "Checking job status..."
Write-Host ""

Start-Sleep -Seconds 1

try {
    $status = Invoke-RestMethod `
        -Uri "http://localhost:8000/job_status/$jobId" `
        -Method Get
} catch {
    Write-Host "ERROR: Failed to retrieve job status."
    Write-Host $_
    exit 1
}

Write-Host "Job status:"
$status | ConvertTo-Json -Depth 10

Write-Host ""
Write-Host "Fetching raw job results..."
Write-Host ""

try {
    $results = Invoke-RestMethod `
        -Uri "http://localhost:8000/results/$jobId" `
        -Method Get
} catch {
    Write-Host "ERROR: Failed to retrieve raw job results."
    Write-Host $_
    exit 1
}

Write-Host "Raw job results:"
$results | ConvertTo-Json -Depth 20

Write-Host ""
Write-Host "Fetching clean route-risk summary..."
Write-Host ""

try {
    $summary = Invoke-RestMethod `
        -Uri "http://localhost:8000/route_risk_summary/$jobId" `
        -Method Get
} catch {
    Write-Host "ERROR: Failed to retrieve clean route-risk summary."
    Write-Host $_
    exit 1
}

Write-Host "Clean route-risk summary:"
$summary | ConvertTo-Json -Depth 20

Write-Host ""
Write-Host "============================================================"
Write-Host "ROUTE RISK SUMMARY HIGHLIGHTS"
Write-Host "============================================================"
Write-Host ""

Write-Host "Route status: $($summary.route_status)"
Write-Host "Route name: $($summary.route_name)"
Write-Host "Origin: $($summary.origin)"
Write-Host "Destination: $($summary.destination)"
Write-Host "Segment count: $($summary.segment_count)"
Write-Host "Coordinate-enabled segment count: $($summary.coordinate_segment_count)"
Write-Host "Route risk score: $($summary.route_risk_score)"
Write-Host "Route risk level: $($summary.route_risk_level)"

if ($summary.highest_risk_segment) {
    Write-Host "Highest-risk segment: $($summary.highest_risk_segment.segment_label)"
    Write-Host "Highest-risk segment latitude: $($summary.highest_risk_segment.latitude)"
    Write-Host "Highest-risk segment longitude: $($summary.highest_risk_segment.longitude)"
    Write-Host "Highest-risk segment score: $($summary.highest_risk_segment.risk_score)"
    Write-Host "Highest-risk segment level: $($summary.highest_risk_segment.risk_level)"
}

Write-Host ""
Write-Host "Summary:"
Write-Host $summary.summary

Write-Host ""
Write-Host "============================================================"
Write-Host "END ROUTE RISK API TEST"
Write-Host "============================================================"
Write-Host ""